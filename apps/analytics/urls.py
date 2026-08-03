from django.urls import path
from . import views
app_name = "analytics"
urlpatterns = [
    path("experiments/<int:experiment_id>/kt.csv", views.kt_export, name="kt_export"),
    path("experiments/<int:experiment_id>/navigation.csv", views.navigation_export, name="navigation_export"),
    path("experiments/<int:experiment_id>/identity.csv", views.identity_export, name="identity_export"),
]
