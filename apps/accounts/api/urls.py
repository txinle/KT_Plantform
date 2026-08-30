from django.urls import path

from .views import CourseContextView


urlpatterns = [
    path("<int:course_id>/context/", CourseContextView.as_view(), name="course-context"),
]
