import csv
from urllib.parse import quote
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404
from django.views.decorators.http import require_POST
from apps.accounts.models import Course, User
from apps.accounts.permissions import role_required
from apps.assessment.models import ExperimentSession, StudentQuestionAssignment
from apps.audit.services import log_action
from .models import Experiment, PublishPreviewParticipant
from .presentation import build_session_report_presentation
from .session_report import build_session_report_document

def _session_report_scope(request, course_pk, experiment_pk, session_pk):
    """Resolve a personal report without exposing cross-course object existence."""

    courses = Course.objects.all()
    if not (request.user.is_superuser or request.user.role == User.Role.ADMIN):
        courses = courses.filter(owner=request.user)
    course = get_object_or_404(courses, pk=course_pk)
    experiment = get_object_or_404(Experiment.objects.filter(course=course), pk=experiment_pk)
    session = get_object_or_404(
        ExperimentSession.objects.select_related("student", "version__experiment"),
        pk=session_pk,
        version__experiment=experiment,
    )
    assignments = (
        StudentQuestionAssignment.objects.filter(session=session)
        .select_related("snapshot", "attempt")
        .order_by("assigned_sequence_index")
    )
    return course, experiment, session, build_session_report_presentation(session, assignments)

def _preview_export_response(plan):
    preview = plan.preview_run
    if preview is None:
        return HttpResponseBadRequest("当前发布计划没有有效预览。")
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="publish-preview-{preview.pk}.csv"'
    )
    response.write("\ufeff")
    writer = csv.writer(response)
    writer.writerow(["班级", "学号", "姓名", "题数", "题目顺序", "校验"])
    participants = (
        PublishPreviewParticipant.objects.filter(class_result__preview_run=preview)
        .select_related("student", "class_result__publish_plan_class__class_group")
        .prefetch_related("items__source_question")
        .order_by("student__student_number", "student_id", "pk")
    )
    for participant in participants:
        items = participant.items.all().order_by("assigned_sequence_index")
        writer.writerow(
            [
                participant.class_result.publish_plan_class.class_group,
                participant.student.student_number,
                participant.student.shown_name,
                participant.item_count,
                " → ".join(item.source_question.code for item in items),
                "通过" if participant.is_valid else "异常",
            ]
        )
    return response

@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def experiment_session_word(request, course_pk, pk, session_pk):
    _course, experiment, session, report = _session_report_scope(
        request,
        course_pk,
        pk,
        session_pk,
    )
    document_bytes = build_session_report_document(report)
    log_action(
        request,
        "experiment.session_word_report",
        experiment,
        {
            "experiment_id": experiment.pk,
            "version": session.version.version_number,
            "session_id": session.pk,
            "student_id": session.student_id,
            "question_count": report.question_count,
        },
    )
    response = HttpResponse(
        document_bytes,
        content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    response["Content-Disposition"] = (
        f"attachment; filename=student-session-report-{session.pk}.docx; "
        f"filename*=UTF-8''{quote(f'学生作答详情-会话-{session.pk}.docx')}"
    )
    return response
