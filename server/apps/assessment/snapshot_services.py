import hashlib

from django.db import transaction

from .models import SimulationSnapshot, _simulation_snapshot_append_scope


@transaction.atomic
def create_simulation_snapshot(
    *,
    attempt,
    simulation_version,
    state_metadata,
    blob_storage_key,
    content_bytes,
    schema_version=1,
):
    if not isinstance(content_bytes, bytes):
        raise TypeError("Simulation snapshot content must be bytes.")
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    existing = SimulationSnapshot.objects.filter(
        attempt=attempt,
        content_hash=content_hash,
    ).first()
    if existing is not None:
        if (
            existing.content_size != len(content_bytes)
            or existing.simulation_version != simulation_version
            or existing.state_metadata != state_metadata
            or existing.blob_storage_key != blob_storage_key
            or existing.schema_version != schema_version
        ):
            raise ValueError(
                "Existing simulation content hash has conflicting snapshot metadata."
            )
        return existing
    snapshot = SimulationSnapshot(
        attempt=attempt,
        simulation_version=simulation_version,
        state_metadata=state_metadata,
        blob_storage_key=blob_storage_key,
        content_hash=content_hash,
        content_size=len(content_bytes),
        schema_version=schema_version,
    )
    with _simulation_snapshot_append_scope():
        snapshot.save(force_insert=True)
    return snapshot
