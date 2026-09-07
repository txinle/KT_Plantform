from django.urls import path

from .views import (
    TeacherCourseCollectionView,
    TeacherCourseDetailView,
    TeacherCourseSettingsView,
)


urlpatterns = [
    path("", TeacherCourseCollectionView.as_view(), name="teacher-course-collection"),
    path("<int:course_id>/", TeacherCourseDetailView.as_view(), name="teacher-course-detail"),
    path(
        "<int:course_id>/settings/",
        TeacherCourseSettingsView.as_view(),
        name="teacher-course-settings",
    ),
]
