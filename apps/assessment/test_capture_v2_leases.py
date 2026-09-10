import json
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest import skipUnless

from django.db import connection, connections
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from apps.question_bank.models import Question

from . import test_response_snapshots as snapshot_fixtures
from .response_snapshot_services import ResponseState
from .writer_lease_services import (
    WriterLeaseConflict,
    acquire_attempt_writer_lease,
    assert_attempt_writer_lease,
    release_attempt_writer_lease,
)


class CaptureV2WriterLeaseTests(TestCase):
    def setUp(self):
        snapshot_fixtures.ResponseSnapshotServiceTests.setUp(self)
        self.attempt = self.attempts[Question.Type.SHORT]
        self.client_instance_id = uuid.uuid4()

    def test_legacy_attempt_without_a_lease_remains_writable(self):
        self.assertTrue(assert_attempt_writer_lease(self.attempt))

    def test_same_client_acquire_reuses_the_active_lease(self):
        now = timezone.now()

        first = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=self.client_instance_id,
            now=now,
        )
        replay = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=self.client_instance_id,
            now=now + timedelta(seconds=1),
        )

        self.assertEqual(replay.token, first.token)
        self.assertEqual(replay.expires_at, first.expires_at)

    def test_expired_lease_can_be_reacquired_and_old_credentials_are_rejected(self):
        now = timezone.now()
        first = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=self.client_instance_id,
            now=now,
        )
        replacement_client = uuid.uuid4()
        replacement = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=replacement_client,
            now=first.expires_at,
        )

        self.assertNotEqual(replacement.token, first.token)
        self.attempt.refresh_from_db()
        with self.assertRaises(WriterLeaseConflict):
            assert_attempt_writer_lease(
                self.attempt,
                lease_token=first.token,
                client_instance_id=self.client_instance_id,
                now=first.expires_at,
            )
        self.assertTrue(
            assert_attempt_writer_lease(
                self.attempt,
                lease_token=replacement.token,
                client_instance_id=replacement_client,
                now=first.expires_at,
            )
        )

    def test_releasing_twice_is_safe_and_another_client_can_acquire_immediately(self):
        now = timezone.now()
        first = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=self.client_instance_id,
            now=now,
        )

        release_attempt_writer_lease(
            self.attempt,
            lease_token=first.token,
            client_instance_id=self.client_instance_id,
        )
        release_attempt_writer_lease(
            self.attempt,
            lease_token=first.token,
            client_instance_id=self.client_instance_id,
        )
        replacement = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=uuid.uuid4(),
            now=now + timedelta(seconds=1),
        )

        self.assertNotEqual(replacement.token, first.token)

    def test_old_release_token_cannot_clear_a_new_active_lease(self):
        now = timezone.now()
        first = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=self.client_instance_id,
            now=now,
        )
        replacement_client = uuid.uuid4()
        replacement = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=replacement_client,
            now=first.expires_at,
        )

        with self.assertRaises(WriterLeaseConflict):
            release_attempt_writer_lease(
                self.attempt,
                lease_token=first.token,
                client_instance_id=self.client_instance_id,
            )
        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.writer_lease_token, replacement.token)
        self.assertEqual(
            self.attempt.writer_client_instance_id,
            replacement_client,
        )

    def test_other_client_gets_a_structured_conflict_instead_of_a_server_error(self):
        acquire_url = reverse("assessment_process:writer_lease_acquire", args=[self.attempt.pk])
        first = self.client.post(
            acquire_url,
            data={"client_instance_id": str(self.client_instance_id)},
            content_type="application/json",
        )
        other = self.client.post(
            acquire_url,
            data={"client_instance_id": str(uuid.uuid4())},
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 201)
        self.assertEqual(other.status_code, 409)
        self.assertEqual(other.json()["error"]["code"], "writer_lease_conflict")


@skipUnless(connection.vendor == "postgresql", "PostgreSQL row locking is required")
class CaptureV2WriterLeaseConcurrencyTests(TransactionTestCase):
    def setUp(self):
        snapshot_fixtures.ResponseSnapshotServiceTests.setUp(self)
        self.attempt = self.attempts[Question.Type.SHORT]

    def test_expired_release_racing_reacquire_never_clears_the_replacement(self):
        first_client = uuid.uuid4()
        replacement_client = uuid.uuid4()
        first = acquire_attempt_writer_lease(
            self.attempt,
            client_instance_id=first_client,
            now=timezone.now(),
        )
        race_time = first.expires_at
        barrier = Barrier(2)

        def release_expired():
            connections.close_all()
            barrier.wait()
            try:
                release_attempt_writer_lease(
                    self.attempt,
                    lease_token=first.token,
                    client_instance_id=first_client,
                    now=race_time,
                )
            except WriterLeaseConflict:
                pass
            finally:
                connections.close_all()

        def acquire_replacement():
            connections.close_all()
            barrier.wait()
            try:
                return acquire_attempt_writer_lease(
                    self.attempt,
                    client_instance_id=replacement_client,
                    now=race_time,
                )
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            release_future = pool.submit(release_expired)
            acquire_future = pool.submit(acquire_replacement)
            replacement = acquire_future.result(timeout=5)
            release_future.result(timeout=5)

        self.attempt.refresh_from_db()
        self.assertEqual(self.attempt.writer_lease_token, replacement.token)
        self.assertEqual(
            self.attempt.writer_client_instance_id,
            replacement_client,
        )


class CaptureV2WriterLeaseEndpointTests(TestCase):
    def setUp(self):
        from .recording_capture_services import ensure_capture_contract_v2

        self.capture_contract = ensure_capture_contract_v2()
        snapshot_fixtures.ResponseSnapshotServiceTests.setUp(self)
        self.attempt = self.attempts[Question.Type.SHORT]

    def transition(self, before, after, *, client, sequence=1):
        kind = self.attempt.assignment.snapshot.question_type
        return {
            "event_id": str(uuid.uuid4()),
            "event_type": "response_transition",
            "event_version": 1,
            "session_id": self.session.pk,
            "attempt_id": self.attempt.pk,
            "client_sequence_no": sequence,
            "client_wall_time_utc": timezone.now().isoformat(),
            "client_monotonic_ms": sequence * 100,
            "client_time_origin": "2026-09-06T00:00:00Z",
            "quality_flags": [],
            "app_version": "capture-v2",
            "payload": {
                "capture_version": "capture-v2",
                "operation_id": str(uuid.uuid4()),
                "branch_id": str(client),
                "parent_event_id": None,
                "before_state": ResponseState(kind, before).state_json,
                "after_state": ResponseState(kind, after).state_json,
                "before_state_hash": ResponseState(kind, before).state_hash,
                "after_state_hash": ResponseState(kind, after).state_hash,
                "input_type": "insertText",
                "source": "student",
            },
        }

    def ingest_raw(self, event, *, client):
        return self.client.post(
            reverse("assessment_process:capture_v2_batch"),
            data=json.dumps({"client_instance_id": str(client), "events": [event]}),
            content_type="application/json",
        )

    def acquire(self, client):
        response = self.client.post(
            reverse("assessment_process:writer_lease_acquire", args=[self.attempt.pk]),
            data=json.dumps({"client_instance_id": str(client)}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def test_release_endpoint_is_idempotent_for_the_same_credentials(self):
        lease = self.acquire(uuid.uuid4())
        release_url = reverse(
            "assessment_process:writer_lease_release",
            args=[self.attempt.pk],
        )
        body = json.dumps(
            {
                "lease_token": lease["lease_token"],
                "client_instance_id": lease["client_instance_id"],
            }
        )

        first = self.client.post(
            release_url,
            data=body,
            content_type="application/json",
        )
        replay = self.client.post(
            release_url,
            data=body,
            content_type="application/json",
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(replay.status_code, 200)

    @staticmethod
    def credentials(lease):
        return {
            "writer_lease_token": lease["lease_token"],
            "writer_client_instance_id": lease["client_instance_id"],
        }

    def test_capture_v2_draft_pointer_accepts_owner_credentials_and_rejects_missing_lease(self):
        stream = uuid.uuid4()
        event = self.transition("", "A", client=stream)
        self.assertEqual(self.ingest_raw(event, client=stream).status_code, 200)
        lease = self.acquire(uuid.uuid4())
        draft_url = reverse("assessment:save_draft", args=[self.attempt.pk])

        saved = self.client.post(
            draft_url,
            data=json.dumps(
                {
                    "answer": "ignored by capture-v2 pointer",
                    "target_event_id": event["event_id"],
                    "expected_version": 0,
                    **self.credentials(lease),
                }
            ),
            content_type="application/json",
        )
        blocked = self.client.post(
            draft_url,
            data=json.dumps(
                {
                    "answer": "ignored by capture-v2 pointer",
                    "target_event_id": event["event_id"],
                    "expected_version": 1,
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(saved.status_code, 200)
        self.assertEqual(saved.json()["active_response_event_id"], event["event_id"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "writer_lease_conflict")

    def test_branch_resolution_passes_owner_credentials_and_rejects_missing_lease(self):
        current_stream = uuid.uuid4()
        candidate_stream = uuid.uuid4()
        current = self.transition("", "A", client=current_stream)
        candidate = self.transition("", "B", client=candidate_stream)
        self.assertEqual(self.ingest_raw(current, client=current_stream).status_code, 200)
        lease = self.acquire(uuid.uuid4())
        self.assertEqual(
            self.client.post(
                reverse("assessment:save_draft", args=[self.attempt.pk]),
                data=json.dumps(
                    {
                        "answer": "ignored by capture-v2 pointer",
                        "target_event_id": current["event_id"],
                        "expected_version": 0,
                        **self.credentials(lease),
                    }
                ),
                content_type="application/json",
            ).status_code,
            200,
        )
        self.assertEqual(self.ingest_raw(candidate, client=candidate_stream).status_code, 200)
        resolve_url = reverse("assessment_process:resolve_branch", args=[self.attempt.pk])
        body = {
            "candidate_event_ids": [current["event_id"], candidate["event_id"]],
            "selected_event_id": candidate["event_id"],
            "expected_version": 1,
            "operation_id": str(uuid.uuid4()),
        }

        resolved = self.client.post(
            resolve_url,
            data=json.dumps({**body, **self.credentials(lease)}),
            content_type="application/json",
        )
        blocked = self.client.post(
            resolve_url,
            data=json.dumps(
                {
                    **body,
                    "selected_event_id": current["event_id"],
                    "expected_version": 2,
                    "operation_id": str(uuid.uuid4()),
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(resolved.status_code, 200)
        self.assertEqual(resolved.json()["active_response_event_id"], candidate["event_id"])
        self.assertEqual(blocked.status_code, 409)
        self.assertEqual(blocked.json()["error"]["code"], "writer_lease_conflict")

    def test_frozen_submission_retries_with_same_token_without_a_lease(self):
        self.attempt = self.attempts[Question.Type.SINGLE]
        stream = uuid.uuid4()
        event = self.transition(None, "A", client=stream)
        self.assertEqual(self.ingest_raw(event, client=stream).status_code, 200)
        lease = self.acquire(uuid.uuid4())
        draft_url = reverse("assessment:save_draft", args=[self.attempt.pk])
        self.assertEqual(
            self.client.post(
                draft_url,
                data=json.dumps(
                    {
                        "answer": "ignored by capture-v2 pointer",
                        "target_event_id": event["event_id"],
                        "expected_version": 0,
                        **self.credentials(lease),
                    }
                ),
                content_type="application/json",
            ).status_code,
            200,
        )
        submission_token = str(uuid.uuid4())
        submit_url = reverse("assessment:submit", args=[self.attempt.pk])
        submitted = self.client.post(
            submit_url,
            data=json.dumps(
                {
                    "submission_token": submission_token,
                    "client_clicked_at": timezone.now().isoformat(),
                    "target_event_id": event["event_id"],
                    **self.credentials(lease),
                }
            ),
            content_type="application/json",
        )
        replay = self.client.post(
            submit_url,
            data=json.dumps(
                {
                    "submission_token": submission_token,
                    "client_clicked_at": timezone.now().isoformat(),
                    "target_event_id": event["event_id"],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(submitted.status_code, 200)
        self.assertEqual(replay.status_code, 200)
        self.attempt.refresh_from_db()
        self.assertEqual(str(self.attempt.submission_token), submission_token)

    def test_raw_capture_batch_still_accepts_another_clients_branch_while_lease_is_active(self):
        lease_owner = uuid.uuid4()
        other_client = uuid.uuid4()
        self.acquire(lease_owner)
        event = self.transition("", "A", client=other_client)

        response = self.ingest_raw(event, client=other_client)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["acknowledgements"][0]["event_id"], event["event_id"])
