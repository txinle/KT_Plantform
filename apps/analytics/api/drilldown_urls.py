from django.urls import path

from .drilldown_views import (
    TeacherAttemptProcessDetailView,
    TeacherQuestionDetailView,
    TeacherStudentDetailView,
    TeacherStudentExperimentDetailView,
)
from .drilldown_compatibility_views import (
    TeacherExperimentResultsResolutionView,
    TeacherExperimentSessionResolutionView,
)


urlpatterns = [
    path(
        "courses/<int:course_id>/experiments/<int:experiment_id>/results/resolve/",
        TeacherExperimentResultsResolutionView.as_view(),
        name="teacher-experiment-results-resolution",
    ),
    path(
        "courses/<int:course_id>/experiments/<int:experiment_id>/sessions/"
        "<int:session_id>/resolve/",
        TeacherExperimentSessionResolutionView.as_view(),
        name="teacher-experiment-session-resolution",
    ),
    path(
        "courses/<int:course_id>/classes/<int:class_id>/students/"
        "<int:student_id>/analysis/",
        TeacherStudentDetailView.as_view(),
        name="teacher-class-student-detail",
    ),
    path(
        "courses/<int:course_id>/classes/<int:class_id>/students/"
        "<int:student_id>/experiments/<int:experiment_id>/",
        TeacherStudentExperimentDetailView.as_view(),
        name="teacher-class-student-experiment-detail",
    ),
    path(
        "courses/<int:course_id>/classes/<int:class_id>/experiments/"
        "<int:experiment_id>/questions/<int:question_id>/",
        TeacherQuestionDetailView.as_view(),
        name="teacher-class-question-detail",
    ),
    path(
        "courses/<int:course_id>/classes/<int:class_id>/experiments/"
        "<int:experiment_id>/questions/<int:question_id>/students/"
        "<int:student_id>/process/",
        TeacherAttemptProcessDetailView.as_view(),
        name="teacher-class-question-process",
    ),
]
