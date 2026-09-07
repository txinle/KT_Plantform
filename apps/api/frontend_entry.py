"""Explicit HTML entry routing. APIs and mutations always remain Django routes."""
import os
from types import SimpleNamespace

from django.conf import settings
from django.http import HttpResponse
from django.utils.deprecation import MiddlewareMixin

from .rollout import is_vue_module_enabled


PAGE_NAMES = {
    'login',
    *(f'courses:{name}' for name in (
        'list', 'create', 'detail', 'settings', 'data', 'data_teaching', 'data_export', 'data_exports',
        'data_quality', 'data_export_history', 'data_advanced', 'data_tables',
        'banks', 'archived_banks', 'shared_sources', 'shared_creators',
        'shared_creator_banks', 'bank_detail', 'bank_edit', 'question_import',
        'kc_import', 'question_edit', 'classes', 'class_detail', 'class_edit',
        'class_student_visibility', 'student_add', 'student_add_manual',
        'student_copy', 'student_import', 'class_student_detail',
        'class_student_experiment_detail', 'class_question_detail', 'class_question_process',
        'experiments', 'experiment_create', 'experiment_detail', 'experiment_edit',
        'experiment_publish_plan', 'experiment_results', 'experiment_session_detail',
    )),
    *(f'accounts:{name}' for name in ('class_list', 'student_list', 'student_import')),
    *(f'question_bank:{name}' for name in (
        'bank_list', 'bank_detail', 'question_import', 'kc_import', 'question_edit', 'concept_list',
    )),
    *(f'experiments:{name}' for name in ('list', 'create', 'detail', 'edit', 'results')),
    *(f'assessment:{name}' for name in (
        'dashboard', 'course', 'learning', 'learning_experiment', 'learning_question', 'question', 'complete',
    )),
}


def is_frontend_page(view_name, method):
    return method in {'GET', 'HEAD'} and view_name in PAGE_NAMES


def frontend_response():
    internal_uri = os.getenv('FRONTEND_INTERNAL_REDIRECT', '')
    if internal_uri:
        response = HttpResponse(content_type='text/html; charset=utf-8')
        response['X-Accel-Redirect'] = internal_uri
    else:
        entry = settings.BASE_DIR / 'frontend' / 'dist' / 'spa-assets' / 'index.html'
        if not entry.is_file():
            return HttpResponse('前端资源尚未构建。请先执行 npm run build。', status=503)
        response = HttpResponse(entry.read_bytes(), content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    return response


class IndependentFrontendMiddleware(MiddlewareMixin):
    def process_view(self, request, view_func, view_args, view_kwargs):
        name = request.resolver_match.view_name
        if not is_frontend_page(name, request.method):
            return None
        if name == 'login':
            if os.getenv('VUE_STANDALONE_ENABLED', '0') == '1':
                return frontend_response()
            return None
        user = request.user
        if not user.is_authenticated:
            return None
        expected_role = 'student' if name.startswith('assessment:') else 'teacher'
        if user.role != expected_role and user.role != 'admin' and not user.is_superuser:
            return None
        course_id = view_kwargs.get('course_pk') or view_kwargs.get('course_id')
        if not course_id and name.startswith('assessment:') and os.getenv('VUE_STANDALONE_COURSE_IDS'):
            from apps.assessment.models import ExperimentSession
            course_id = ExperimentSession.objects.filter(
                pk=view_kwargs.get('session_id'), student=user,
            ).values_list('version__experiment__course_id', flat=True).first()
        course = SimpleNamespace(pk=course_id) if course_id else None
        if not is_vue_module_enabled('standalone', user, course=course):
            return None
        request._kt_standalone = True
        # The question view checks ownership/assignment and adds the authorized
        # capture contract header used by the offline worker.
        if name in {'assessment:question', 'courses:data_teaching'}:
            return None
        return frontend_response()
