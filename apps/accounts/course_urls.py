from django.urls import path

from . import course_views
from . import course_resource_views
from apps.question_bank import course_views as bank_views
from apps.experiments import course_views as experiment_views

app_name = "courses"

urlpatterns = [
    path("", course_views.course_list, name="list"),
    path("create/", course_views.course_create, name="create"),
    path("<int:course_pk>/", course_views.course_detail, name="detail"),
    path("<int:course_pk>/data/", course_views.course_data, name="data"),
    path("<int:course_pk>/settings/", course_views.course_settings, name="settings"),
    path("<int:course_pk>/archive/", course_views.course_archive, name="archive"),
    path("<int:course_pk>/banks/", bank_views.bank_list, name="banks"),
    path("<int:course_pk>/banks/<int:pk>/", bank_views.bank_detail, name="bank_detail"),
    path("<int:course_pk>/banks/<int:pk>/import/", bank_views.question_import, name="question_import"),
    path("<int:course_pk>/banks/<int:pk>/kc-import/", bank_views.kc_import, name="kc_import"),
    path("<int:course_pk>/banks/<int:pk>/visibility/", bank_views.bank_visibility_update, name="bank_visibility"),
    path("<int:course_pk>/banks/<int:bank_pk>/questions/<int:question_pk>/edit/", bank_views.question_edit, name="question_edit"),
    path("<int:course_pk>/banks/copy/<int:source_pk>/", bank_views.copy_bank, name="copy_bank"),
    path("<int:course_pk>/concepts/create/", bank_views.concept_create, name="concept_create"),
    path("<int:course_pk>/concepts/<int:concept_pk>/rename/", bank_views.concept_rename, name="concept_rename"),
    path("<int:course_pk>/concepts/<int:concept_pk>/delete/", bank_views.concept_delete, name="concept_delete"),
    path("<int:course_pk>/concepts/search/", bank_views.concept_search, name="concept_search"),
    path("<int:course_pk>/classes/", course_resource_views.class_list, name="classes"),
    path("<int:course_pk>/classes/<int:class_pk>/", course_resource_views.class_detail, name="class_detail"),
    path("<int:course_pk>/classes/<int:class_pk>/edit/", course_resource_views.class_edit, name="class_edit"),
    path("<int:course_pk>/classes/<int:class_pk>/rename/", course_resource_views.class_rename, name="class_rename"),
    path("<int:course_pk>/classes/<int:class_pk>/students/add/", course_resource_views.student_add, name="student_add"),
    path("<int:course_pk>/classes/<int:class_pk>/students/add/manual/", course_resource_views.student_add_manual, name="student_add_manual"),
    path("<int:course_pk>/classes/<int:class_pk>/students/copy/", course_resource_views.student_copy, name="student_copy"),
    path("<int:course_pk>/classes/<int:class_pk>/students/import-template.xlsx", course_resource_views.student_import_template, name="student_import_template"),
    path("<int:course_pk>/classes/<int:class_pk>/students/import/", course_resource_views.student_import, name="student_import"),
    path("<int:course_pk>/classes/<int:class_pk>/students/<int:student_pk>/move/", course_resource_views.move_student, name="move_student"),
    path("<int:course_pk>/classes/<int:class_pk>/students/<int:student_pk>/reset-password/", course_resource_views.reset_student_password, name="reset_password"),
    path("<int:course_pk>/experiments/", experiment_views.experiment_list, name="experiments"),
    path("<int:course_pk>/experiments/create/", experiment_views.experiment_create, name="experiment_create"),
    path("<int:course_pk>/experiments/<int:pk>/", experiment_views.experiment_detail, name="experiment_detail"),
    path("<int:course_pk>/experiments/<int:pk>/edit/", experiment_views.experiment_edit, name="experiment_edit"),
    path("<int:course_pk>/experiments/<int:pk>/sync-questions/", experiment_views.experiment_sync_questions, name="experiment_sync_questions"),
    path("<int:course_pk>/experiments/<int:pk>/assign/", experiment_views.experiment_assign, name="experiment_assign"),
    path("<int:course_pk>/experiments/<int:pk>/publish/", experiment_views.experiment_publish, name="experiment_publish"),
    path("<int:course_pk>/experiments/<int:pk>/results/", experiment_views.experiment_results, name="experiment_results"),
]
