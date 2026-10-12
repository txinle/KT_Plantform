from django.urls import path

from . import process_views
from .api.domain_adapter import documented_domain_view
from .api.mutation_schema import PROCESS


app_name = "assessment_process"


def documented(name):
    return documented_domain_view(getattr(process_views, name), **PROCESS[name])

urlpatterns = [
    path("capture-v2/attempts/<int:attempt_id>/tools/", documented('research_tools'), name="research_tools"),
    path("capture-v2/attempts/<int:attempt_id>/resolve/", documented('resolve_branch'), name="resolve_branch"),
    path("capture-v2/batch/", documented('capture_v2_batch'), name="capture_v2_batch"),
    path("events/batch/", documented('event_batch'), name="event_batch"),
    path("capture-checkpoints/", documented('capture_checkpoint'), name="capture_checkpoint"),
    path(
        "time-sync/create/",
        documented('time_sync_create'),
        name="time_sync_create",
    ),
    path(
        "time-sync/finalize/",
        documented('time_sync_finalize'),
        name="time_sync_finalize",
    ),
    path(
        "attempts/<int:attempt_id>/writer-lease/acquire/",
        documented('writer_lease_acquire'),
        name="writer_lease_acquire",
    ),
    path(
        "attempts/<int:attempt_id>/writer-lease/renew/",
        documented('writer_lease_renew'),
        name="writer_lease_renew",
    ),
    path(
        "attempts/<int:attempt_id>/writer-lease/release/",
        documented('writer_lease_release'),
        name="writer_lease_release",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/prompt-shown/",
        documented('confidence_prompt_shown'),
        name="confidence_prompt_shown",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/commit/",
        documented('confidence_commit'),
        name="confidence_commit",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/skip/",
        documented('confidence_skip'),
        name="confidence_skip",
    ),
]
