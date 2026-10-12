from django.urls import path

from apps.api.downloads import CSV, XLSX, download_view
from .. import downloads
from ..export_fields import FIELD_LABELS


FIELDS = {'application/x-www-form-urlencoded': {
    'type': 'object', 'required': ['fields'], 'properties': {'fields': {
        'type': 'array', 'minItems': 1, 'items': {'type': 'string', 'enum': list(FIELD_LABELS)},
        'description': 'Repeated form fields. Required identity columns are retained by the server.',
    }},
}}

urlpatterns = [
    path('courses/<int:course_id>/data/export-jobs/<uuid:job_id>/download/',
         download_view(downloads.export_job_download, media_types=('application/zip', XLSX),
                       operation_id='teacher_export_job_download', kwarg_map={'course_id': 'course_pk'}),
         name='teacher-export-job-download'),
    path('experiments/<int:experiment_id>/kt.csv', download_view(downloads.kt_export,
         operation_id='teacher_experiment_kt_export'), name='teacher-experiment-kt-export'),
    path('experiments/<int:experiment_id>/navigation.csv', download_view(downloads.navigation_export,
         operation_id='teacher_experiment_navigation_export'), name='teacher-experiment-navigation-export'),
    path('experiments/<int:experiment_id>/identity.csv', download_view(downloads.identity_export,
         operation_id='teacher_experiment_identity_export'), name='teacher-experiment-identity-export'),
    path('experiments/<int:experiment_id>/identified.xlsx', download_view(downloads.identified_export,
         method='post', media_types=(XLSX,), operation_id='teacher_experiment_identified_export', request_schema=FIELDS),
         name='teacher-experiment-identified-export'),
    path('experiments/<int:experiment_id>/navigation-identified.csv', download_view(downloads.identified_navigation_export,
         method='post', operation_id='teacher_experiment_identified_navigation_export'),
         name='teacher-experiment-identified-navigation-export'),
]
