from django.urls import path
from . import views
app_name = "assessment"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("experiments/<int:experiment_id>/start/", views.start_experiment, name="start"),
    path("sessions/<int:session_id>/questions/<int:assignment_id>/", views.question_page, name="question"),
    path("attempts/<int:attempt_id>/draft/", views.save_draft_api, name="save_draft"),
    path("attempts/<int:attempt_id>/submit/", views.submit_api, name="submit"),
    path("attempts/<int:attempt_id>/skip/", views.skip_api, name="skip"),
    path("sessions/<int:session_id>/finish/", views.finish, name="finish"),
    path("sessions/<int:session_id>/complete/", views.complete, name="complete"),
]
