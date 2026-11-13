from io import BytesIO

from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_GET
from apps.accounts.permissions import role_required
from .services.import_templates import (
    QUESTION_IMAGE_EXAMPLES_FILENAME,
    build_question_group_template, build_simple_blank_template,
    build_question_image_examples_zip,
    build_question_import_template_csv,
    build_question_import_template_excel,
    build_question_import_template_word,
)

def _fixture_download(filename):
    path = settings.BASE_DIR / "fixtures" / filename
    if not path.is_file():
        raise Http404("示例文件不存在")
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="text/csv; charset=utf-8",
    )

def _memory_download(content, filename, content_type):
    response = HttpResponse(content, content_type=content_type)
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response

@role_required("teacher", "admin")
@require_GET
def question_import_template(request):
    return FileResponse(BytesIO(build_question_import_template_csv()), as_attachment=True,
                        filename='question_import_template.csv', content_type='text/csv; charset=utf-8')


@role_required('teacher', 'admin')
@require_GET
def question_image_examples(request):
    return _memory_download(build_question_image_examples_zip(), QUESTION_IMAGE_EXAMPLES_FILENAME, 'application/zip')

@role_required("teacher", "admin")
@require_GET
def question_import_template_excel(request):
    return _memory_download(
        build_question_import_template_excel(),
        "question_import_template.xlsx",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )

@role_required("teacher", "admin")
@require_GET
def question_import_template_word(request):
    return _memory_download(
        build_question_import_template_word(),
        "question_import_template.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )


@role_required('teacher', 'admin')
@require_GET
def question_group_import_template(request, extension):
    content_types = {'csv': 'text/csv; charset=utf-8',
        'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'}
    if extension not in content_types:
        raise Http404('不支持的模板格式')
    builder = build_simple_blank_template if request.GET.get('layout') == 'blanks' else build_question_group_template
    return _memory_download(builder(extension), f'question_groups_template.{extension}', content_types[extension])

@role_required("teacher", "admin")
@require_GET
def kc_import_template(request):
    return _fixture_download("kc_mapping_template.csv")

@role_required("teacher", "admin")
@require_GET
def course_kc_import_template(request):
    return _fixture_download("course_kc_template.csv")
