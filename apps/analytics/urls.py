from django.urls import path
from . import views
app_name = "analytics"
urlpatterns = [
    path("courses/<int:course_pk>/export-jobs/create/", views.export_job_create, name="export_job_create"),
    path("courses/<int:course_pk>/purge-requests/create/", views.purge_request_create, name="purge_request_create"),
    path("courses/<int:course_pk>/export-jobs/<uuid:job_id>/download/", views.export_job_download, name="export_job_download"),
    path("experiments/<int:experiment_id>/kt.csv", views.kt_export, name="kt_export"),
    path("experiments/<int:experiment_id>/navigation.csv", views.navigation_export, name="navigation_export"),
    path("experiments/<int:experiment_id>/identity.csv", views.identity_export, name="identity_export"),
    path("experiments/<int:experiment_id>/identified.xlsx", views.identified_export, name="identified_export"),
    path("experiments/<int:experiment_id>/navigation-identified.csv", views.identified_navigation_export, name="identified_navigation_export"),
]
