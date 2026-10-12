import uuid
from datetime import datetime, timedelta, timezone as datetime_timezone
from decimal import Decimal
from unittest.mock import patch

from django.test import TestCase

from .models import ClientTimeSync


class ClientTimeSyncServiceTests(TestCase):
    def setUp(self):
        self.client_instance_id = uuid.uuid4()
        self.client_wall_time = datetime(
            2026,
            8,
            26,
            4,
            0,
            0,
            tzinfo=datetime_timezone.utc,
        )
        self.client_time_origin = self.client_wall_time - timedelta(seconds=10)
        self.server_received = self.client_wall_time + timedelta(milliseconds=120)
        self.server_responded = self.client_wall_time + timedelta(milliseconds=140)

    def create_pending(self):
        from .time_sync_services import create_pending_time_sync

        with patch(
            "apps.assessment.time_sync_services.timezone.now",
            side_effect=[
                self.server_received,
                self.server_responded,
                self.server_responded,
            ],
        ):
            return create_pending_time_sync(
                client_instance_id=self.client_instance_id,
                client_wall_time_utc=self.client_wall_time,
                client_monotonic_ms=Decimal("10000.000"),
                client_time_origin=self.client_time_origin,
            )

    def finalize(self, sample, **overrides):
        from .time_sync_services import finalize_time_sync

        values = {
            "time_sync_id": sample.pk,
            "client_instance_id": self.client_instance_id,
            "client_received_wall_time_utc": self.client_wall_time
            + timedelta(milliseconds=60),
            "client_received_monotonic_ms": Decimal("10060.000"),
        }
        values.update(overrides)
        with patch(
            "apps.assessment.time_sync_services.timezone.now",
            return_value=self.server_responded + timedelta(milliseconds=30),
        ):
            return finalize_time_sync(**values)

    def test_create_preserves_raw_observations_as_pending_without_estimates(self):
        sample = self.create_pending()

        self.assertEqual(sample.sync_quality, ClientTimeSync.SyncQuality.PENDING)
        self.assertEqual(sample.server_received_at, self.server_received)
        self.assertEqual(sample.server_responded_at, self.server_responded)
        self.assertIsNone(sample.client_received_wall_time_utc)
        self.assertIsNone(sample.client_received_monotonic_ms)
        self.assertIsNone(sample.estimated_clock_offset_ms)
        self.assertIsNone(sample.estimated_rtt_ms)
        self.assertIsNone(sample.finalized_at)

    def test_finalize_computes_network_rtt_and_ntp_midpoint_offset(self):
        sample = self.finalize(self.create_pending())

        self.assertEqual(sample.sync_quality, ClientTimeSync.SyncQuality.VALID)
        self.assertEqual(sample.estimated_rtt_ms, 40)
        self.assertEqual(sample.estimated_clock_offset_ms, 100)
        self.assertEqual(
            sample.client_received_monotonic_ms,
            Decimal("10060.000"),
        )
        self.assertIsNotNone(sample.finalized_at)

    def test_finalize_marks_wall_clock_discontinuity_without_estimates(self):
        sample = self.finalize(
            self.create_pending(),
            client_received_wall_time_utc=self.client_wall_time
            + timedelta(minutes=5, milliseconds=60),
        )

        self.assertEqual(
            sample.sync_quality,
            ClientTimeSync.SyncQuality.CLOCK_CHANGED,
        )
        self.assertIsNone(sample.estimated_rtt_ms)
        self.assertIsNone(sample.estimated_clock_offset_ms)
        self.assertEqual(
            sample.client_received_wall_time_utc,
            self.client_wall_time + timedelta(minutes=5, milliseconds=60),
        )

    def test_finalize_marks_negative_or_implausible_round_trip_invalid(self):
        for received_monotonic in (Decimal("9999.000"), Decimal("80001.000")):
            with self.subTest(received_monotonic=received_monotonic):
                sample = self.finalize(
                    self.create_pending(),
                    client_received_monotonic_ms=received_monotonic,
                )
                self.assertEqual(
                    sample.sync_quality,
                    ClientTimeSync.SyncQuality.INVALID,
                )
                self.assertIsNone(sample.estimated_rtt_ms)
                self.assertIsNone(sample.estimated_clock_offset_ms)

    def test_finalize_rejects_cross_instance_observations_without_mutation(self):
        sample = self.create_pending()

        with self.assertRaises(ValueError):
            self.finalize(sample, client_instance_id=uuid.uuid4())

        sample.refresh_from_db()
        self.assertEqual(sample.sync_quality, ClientTimeSync.SyncQuality.PENDING)
        self.assertIsNone(sample.finalized_at)

    def test_identical_finalize_is_idempotent_but_conflicting_rewrite_is_rejected(self):
        sample = self.create_pending()
        first = self.finalize(sample)
        replay = self.finalize(sample)

        self.assertEqual(replay.pk, first.pk)
        with self.assertRaises(ValueError):
            self.finalize(
                sample,
                client_received_monotonic_ms=Decimal("10061.000"),
            )
