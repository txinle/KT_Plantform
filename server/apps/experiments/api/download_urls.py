from django.shortcuts import get_object_or_404
from django.urls import path
from apps.accounts.course_access import get_accessible_course
from apps.api.downloads import DOCX, download_view
from ..downloads import _preview_export_response, experiment_session_word
from ..models import PublishPlan


def preview_export(request, plan_id):
    plan = get_object_or_404(PublishPlan.objects.select_related('experiment__course', 'preview_run'), pk=plan_id)
    # Preserve the old page's accessible-course boundary, including read-only collaborators.
    get_accessible_course(request.user, plan.experiment.course_id)
    return _preview_export_response(plan)


urlpatterns = [
    path('courses/<int:course_id>/experiments/<int:experiment_id>/sessions/<int:session_id>/word/',
         download_view(experiment_session_word, method='post', media_types=(DOCX,),
                       operation_id='teacher_experiment_session_word',
                       kwarg_map={'course_id': 'course_pk', 'experiment_id': 'pk', 'session_id': 'session_pk'}),
         name='teacher-experiment-session-word'),
    path('publish-plans/<int:plan_id>/preview/export/', download_view(preview_export,
         operation_id='teacher_publish_preview_export'), name='teacher-publish-preview-export'),
]
