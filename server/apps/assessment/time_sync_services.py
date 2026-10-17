from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from django.db import transaction
from django.utils import timezone

from .models import ClientTimeSync, ExperimentSession


MAX_SYNC_ROUND_TRIP_MS = Decimal("60000")
CLOCK_DISCONTINUITY_THRESHOLD_MS = Decimal("2000")


def _as_finite_decimal(value, *, field_name):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as error:
        raise ValueError(f"{field_name} must be a finite decimal.") from error
    if not result.is_finite():
        raise ValueError(f"{field_name} must be a finite decimal.")
    return result


def _duration_ms(delta):
    return Decimal(str(delta.total_seconds())) * Decimal("1000")


def _rounded_integer(value):
    return int(value.to_integral_value(rounding=ROUND_HALF_UP))


@transaction.atomic
def create_pending_time_sync(
    *,
    client_instance_id,
    client_wall_time_utc,
    client_monotonic_ms,
    client_time_origin,
    session=None,
):
    monotonic_ms = _as_finite_decimal(
        client_monotonic_ms,
        field_name="client_monotonic_ms",
    )
    if monotonic_ms < 0:
        raise ValueError("client_monotonic_ms cannot be negative.")
    server_received_at = timezone.now()
    server_responded_at = timezone.now()
    sample = ClientTimeSync(
        session=session,
        client_instance_id=client_instance_id,
        client_wall_time_utc=client_wall_time_utc,
        client_monotonic_ms=monotonic_ms,
        client_time_origin=client_time_origin,
        server_received_at=server_received_at,
        server_responded_at=server_responded_at,
        sync_quality=ClientTimeSync.SyncQuality.PENDING,
    )
    sample.full_clean(validate_unique=False, validate_constraints=False)
    sample.save(force_insert=True)
    if sample.session_id is not None and ExperimentSession.objects.filter(
        pk=sample.session_id,
        status__in=(ExperimentSession.Status.COMPLETED, ExperimentSession.Status.EXPIRED),
    ).exists():
        from apps.analytics.research_materialization_jobs import schedule_session_materialization

        schedule_session_materialization(sample.session_id)
    return sample


def _same_finalize_observation(
    sample,
    *,
    client_received_wall_time_utc,
    client_received_monotonic_ms,
):
    return (
        sample.client_received_wall_time_utc == client_received_wall_time_utc
        and sample.client_received_monotonic_ms == client_received_monotonic_ms
    )


@transaction.atomic
def finalize_time_sync(
    *,
    time_sync_id,
    client_instance_id,
    client_received_wall_time_utc,
    client_received_monotonic_ms,
):
    received_monotonic_ms = _as_finite_decimal(
        client_received_monotonic_ms,
        field_name="client_received_monotonic_ms",
    )
    sample = ClientTimeSync.objects.select_for_update().get(pk=time_sync_id)
    if sample.client_instance_id != client_instance_id:
        raise ValueError("Time sync sample belongs to another client instance.")
    if sample.sync_quality != ClientTimeSync.SyncQuality.PENDING:
        if _same_finalize_observation(
            sample,
            client_received_wall_time_utc=client_received_wall_time_utc,
            client_received_monotonic_ms=received_monotonic_ms,
        ):
            return sample
        raise ValueError("A finalized time sync sample cannot be rewritten.")

    sample.client_received_wall_time_utc = client_received_wall_time_utc
    sample.client_received_monotonic_ms = received_monotonic_ms
    sample.finalized_at = timezone.now()
    sample.estimated_clock_offset_ms = None
    sample.estimated_rtt_ms = None

    client_round_trip_ms = received_monotonic_ms - sample.client_monotonic_ms
    if client_round_trip_ms < 0 or client_round_trip_ms > MAX_SYNC_ROUND_TRIP_MS:
        sample.sync_quality = ClientTimeSync.SyncQuality.INVALID
    else:
        client_wall_elapsed_ms = _duration_ms(
            client_received_wall_time_utc - sample.client_wall_time_utc
        )
        if (
            abs(client_wall_elapsed_ms - client_round_trip_ms)
            > CLOCK_DISCONTINUITY_THRESHOLD_MS
        ):
            sample.sync_quality = ClientTimeSync.SyncQuality.CLOCK_CHANGED
        else:
            server_processing_ms = _duration_ms(
                sample.server_responded_at - sample.server_received_at
            )
            network_round_trip_ms = client_round_trip_ms - server_processing_ms
            if (
                network_round_trip_ms < 0
                or network_round_trip_ms > MAX_SYNC_ROUND_TRIP_MS
            ):
                sample.sync_quality = ClientTimeSync.SyncQuality.INVALID
            else:
                offset_ms = (
                    _duration_ms(
                        sample.server_received_at - sample.client_wall_time_utc
                    )
                    + _duration_ms(
                        sample.server_responded_at
                        - client_received_wall_time_utc
                    )
                ) / Decimal("2")
                sample.sync_quality = ClientTimeSync.SyncQuality.VALID
                sample.estimated_clock_offset_ms = _rounded_integer(offset_ms)
                sample.estimated_rtt_ms = _rounded_integer(network_round_trip_ms)

    sample.full_clean(validate_unique=False, validate_constraints=True)
    sample.save(
        update_fields=(
            "client_received_wall_time_utc",
            "client_received_monotonic_ms",
            "estimated_clock_offset_ms",
            "estimated_rtt_ms",
            "sync_quality",
            "finalized_at",
        )
    )
    if sample.session_id is not None and ExperimentSession.objects.filter(
        pk=sample.session_id,
        status__in=(ExperimentSession.Status.COMPLETED, ExperimentSession.Status.EXPIRED),
    ).exists():
        from apps.analytics.research_materialization_jobs import schedule_session_materialization

        schedule_session_materialization(sample.session_id)
    return sample
