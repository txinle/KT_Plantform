from django.urls import path

from .teaching_legacy_views import TeacherTeachingLegacyView


urlpatterns = [
    path(
        "courses/<int:course_id>/data/teaching/",
        TeacherTeachingLegacyView.as_view(),
        name="teacher-legacy-teaching",
    ),
]
