from django.urls import path
from . import views

app_name = "accounts"
urlpatterns = [
    path("classes/", views.class_list, name="class_list"),
    path("students/", views.student_list, name="student_list"),
    path("students/import/", views.student_import, name="student_import"),
    path("students/import-template.xlsx", views.student_import_template, name="student_import_template"),
    path("students/<int:pk>/reset-password/", views.reset_student_password, name="reset_password"),
]
