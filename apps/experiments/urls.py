from django.urls import path
from . import views
app_name = "experiments"
urlpatterns = [
    path("", views.experiment_list, name="list"),
    path("create/", views.experiment_create, name="create"),
    path("<int:pk>/", views.experiment_detail, name="detail"),
    path("<int:pk>/edit/", views.experiment_edit, name="edit"),
    path("<int:pk>/assign/", views.experiment_assign, name="assign"),
    path("<int:pk>/publish/", views.experiment_publish, name="publish"),
    path("<int:pk>/delete/", views.experiment_delete, name="delete"),
    path("<int:pk>/sync-questions/", views.experiment_sync_questions, name="sync_questions"),
    path("<int:pk>/results/", views.experiment_results, name="results"),
]
