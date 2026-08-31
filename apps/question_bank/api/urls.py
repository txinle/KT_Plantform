from django.urls import path

from .views import (
    TeacherBankConceptCollectionView,
    TeacherCourseConceptCollectionView,
    TeacherQuestionBankCollectionView,
    TeacherQuestionBankDetailView,
    TeacherQuestionCollectionView,
    TeacherCourseKCImportConfirmView,
    TeacherCourseKCImportPreviewView,
    TeacherQuestionKCImportConfirmView,
    TeacherQuestionKCImportPreviewView,
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
        "courses/<int:course_id>/concepts/import/preview/",
        TeacherCourseKCImportPreviewView.as_view(),
        name="teacher-course-kc-import-preview",
    ),
    path(
        "courses/<int:course_id>/concepts/import/confirm/",
        TeacherCourseKCImportConfirmView.as_view(),
        name="teacher-course-kc-import-confirm",
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
    path(
        "courses/<int:course_id>/banks/<int:bank_id>/concepts/import/preview/",
        TeacherQuestionKCImportPreviewView.as_view(),
        name="teacher-question-kc-import-preview",
    ),
    path(
        "courses/<int:course_id>/banks/<int:bank_id>/concepts/import/confirm/",
        TeacherQuestionKCImportConfirmView.as_view(),
        name="teacher-question-kc-import-confirm",
    ),
]
