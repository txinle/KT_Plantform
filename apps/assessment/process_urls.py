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
]
