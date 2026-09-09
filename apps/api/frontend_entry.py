"""Explicit HTML entry routing. APIs and mutations always remain Django routes."""
import os

from django.conf import settings
from django.http import HttpResponse


PAGE_NAMES = {
    'login',
    'password-change',
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
    elif settings.DEBUG and settings.VITE_DEV_SERVER_URL:
        entry = settings.BASE_DIR / 'frontend' / 'index.html'
        vite_url = settings.VITE_DEV_SERVER_URL.rstrip('/')
        content = entry.read_text(encoding='utf-8').replace(
            '<script type="module" src="/src/main.ts"></script>',
            (
                f'<script type="module" src="{vite_url}/@vite/client"></script>\n'
                f'    <script type="module" src="{vite_url}/src/main.ts"></script>'
            ),
        )
        response = HttpResponse(content, content_type='text/html; charset=utf-8')
    else:
        entry = settings.BASE_DIR / 'frontend' / 'dist' / 'spa-assets' / 'index.html'
        if not entry.is_file():
            return HttpResponse('前端资源尚未构建。请先执行 npm run build。', status=503)
        response = HttpResponse(entry.read_bytes(), content_type='text/html; charset=utf-8')
    response['Cache-Control'] = 'no-store'
    return response
