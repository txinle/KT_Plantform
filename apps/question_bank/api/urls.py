from django.urls import path

from .views import (
    TeacherBankConceptCollectionView,
    TeacherCourseConceptCollectionView,
    TeacherQuestionBankCollectionView,
    TeacherQuestionBankDetailView,
    TeacherQuestionCollectionView,
)


urlpatterns = [
    path(
        "courses/<int:course_id>/banks/",
        TeacherQuestionBankCollectionView.as_view(),
        name="teacher-question-bank-collection",
    ),
    path(
        "courses/<int:course_id>/concepts/",
        TeacherCourseConceptCollectionView.as_view(),
        name="teacher-course-concept-collection",
    ),
    path(
        "courses/<int:course_id>/banks/<int:bank_id>/",
        TeacherQuestionBankDetailView.as_view(),
        name="teacher-question-bank-detail",
    ),
    path(
        "courses/<int:course_id>/banks/<int:bank_id>/questions/",
        TeacherQuestionCollectionView.as_view(),
        name="teacher-question-collection",
    ),
    path(
        "courses/<int:course_id>/banks/<int:bank_id>/concepts/",
        TeacherBankConceptCollectionView.as_view(),
        name="teacher-bank-concept-collection",
    ),
]

