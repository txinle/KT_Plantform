from django.conf import settings
from django.http import FileResponse, Http404, HttpResponse
from django.views.decorators.http import require_GET
from apps.accounts.permissions import role_required
from .services.import_templates import build_question_import_template_excel, build_question_import_template_word

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
    return _fixture_download("question_import_template.csv")

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

@role_required("teacher", "admin")
@require_GET
def kc_import_template(request):
    return _fixture_download("kc_mapping_template.csv")

@role_required("teacher", "admin")
@require_GET
def course_kc_import_template(request):
    return _fixture_download("course_kc_template.csv")
