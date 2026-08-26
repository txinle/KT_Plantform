from django.urls import path

from . import process_views


app_name = "assessment_process"

urlpatterns = [
    path("events/batch/", process_views.event_batch, name="event_batch"),
    path(
        "time-sync/create/",
        process_views.time_sync_create,
        name="time_sync_create",
    ),
    path(
        "time-sync/finalize/",
        process_views.time_sync_finalize,
        name="time_sync_finalize",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/prompt-shown/",
        process_views.confidence_prompt_shown,
        name="confidence_prompt_shown",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/commit/",
        process_views.confidence_commit,
        name="confidence_commit",
    ),
    path(
        "attempts/<int:attempt_id>/confidence/skip/",
        process_views.confidence_skip,
        name="confidence_skip",
    ),
]
