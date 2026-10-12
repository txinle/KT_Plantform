from django.urls import path

from .views import (
    TeacherCourseArchiveView,
    TeacherCourseCollectionView,
    TeacherCourseDetailView,
    TeacherCoursePurgeCreateView,
    TeacherCoursePurgeExecuteView,
    TeacherCourseSettingsView,
)


urlpatterns = [
    path("", TeacherCourseCollectionView.as_view(), name="teacher-course-collection"),
    path("<int:course_id>/", TeacherCourseDetailView.as_view(), name="teacher-course-detail"),
    path(
        "<int:course_id>/archive/",
        TeacherCourseArchiveView.as_view(),
        name="teacher-course-archive",
    ),
    path(
        "<int:course_id>/purge-requests/",
        TeacherCoursePurgeCreateView.as_view(),
        name="teacher-course-purge-create",
    ),
    path(
        "<int:course_id>/purge-requests/<uuid:purge_request_id>/execute/",
        TeacherCoursePurgeExecuteView.as_view(),
        name="teacher-course-purge-execute",
    ),
    path(
        "<int:course_id>/settings/",
        TeacherCourseSettingsView.as_view(),
        name="teacher-course-settings",
    ),
]
