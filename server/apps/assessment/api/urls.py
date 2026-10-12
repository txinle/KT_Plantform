from django.urls import path
from .session_summary import StudentSessionSummaryView
from .recovery import StudentAttemptRecoveryView, StudentTaskRecoveryView

from .views import (
    StudentCourseCollectionView,
    StudentLearningExperimentView,
    StudentLearningQuestionView,
    StudentLearningView,
    StudentTaskCollectionView,
    StudentTaskResumeView,
    StudentTaskResultView,
    StudentTaskStartView,
    StudentRuntimeQuestionEntryView,
    StudentAdmissionStatusView,
)


urlpatterns = [
    path('sessions/<int:session_id>/questions/<int:assignment_id>/recovery/', StudentAttemptRecoveryView.as_view(), name='student-question-recovery'),
    path('attempts/<int:attempt_id>/recovery/', StudentAttemptRecoveryView.as_view(), name='student-attempt-recovery'),
    path('tasks/<int:task_id>/recovery/', StudentTaskRecoveryView.as_view(), name='student-task-recovery'),
    path('sessions/<int:session_id>/summary/', StudentSessionSummaryView.as_view(), name='student-session-summary'),
    path("courses/", StudentCourseCollectionView.as_view(), name="student-courses"),
    path(
        "courses/<int:course_id>/tasks/",
        StudentTaskCollectionView.as_view(),
        name="student-tasks",
    ),
    path(
        "courses/<int:course_id>/learning/",
        StudentLearningView.as_view(),
        name="student-learning",
    ),
    path(
        "courses/<int:course_id>/learning/experiments/<int:experiment_id>/",
        StudentLearningExperimentView.as_view(),
        name="student-learning-experiment",
    ),
    path(
        "courses/<int:course_id>/learning/experiments/<int:experiment_id>/questions/<int:question_id>/",
        StudentLearningQuestionView.as_view(),
        name="student-learning-question",
    ),
    path(
        "tasks/<int:task_id>/start/",
        StudentTaskStartView.as_view(),
        name="student-task-start",
    ),
    path(
        "tasks/<int:task_id>/resume/",
        StudentTaskResumeView.as_view(),
        name="student-task-resume",
    ),
    path(
        "tasks/<int:task_id>/admission/status/",
        StudentAdmissionStatusView.as_view(),
        name="student-task-admission-status",
    ),
    path(
        "tasks/<int:task_id>/results/",
        StudentTaskResultView.as_view(),
        name="student-task-results",
    ),
    path(
        "sessions/<int:session_id>/questions/<int:assignment_id>/",
        StudentRuntimeQuestionEntryView.as_view(),
        name="student-runtime-question",
    ),
]
