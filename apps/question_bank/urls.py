from django.urls import path
from . import views
app_name = "question_bank"
urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("import-templates/questions.csv", views.question_import_template, name="question_import_template"),
    path("import-templates/kcs.csv", views.kc_import_template, name="kc_import_template"),
    path("import-templates/course-kcs.csv", views.course_kc_import_template, name="course_kc_import_template"),
    path("banks/", views.bank_list, name="bank_list"),
    path("banks/<int:pk>/", views.bank_detail, name="bank_detail"),
    path("banks/<int:pk>/visibility/", views.bank_visibility_update, name="bank_visibility_update"),
    path("banks/<int:pk>/import/", views.question_import, name="question_import"),
    path("banks/<int:pk>/kc-import/", views.kc_import, name="kc_import"),
    path("banks/<int:bank_pk>/questions/<int:question_pk>/edit/", views.question_edit, name="question_edit"),
    path("concepts/", views.concept_list, name="concept_list"),
    path("banks/<int:pk>/copy/", views.copy_bank, name="copy_bank"),
]
