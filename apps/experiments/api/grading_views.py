from django.http import Http404
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.api.exceptions import ApiBusinessError, ApiConflict
from apps.api.pagination import FixedPageNumberPagination
from apps.api.permissions import RolePermission
from apps.accounts.models import User
from apps.assessment.manual_grading_services import (
    ManualGradingError,
    grade_task_short_answer,
    grading_submission_projection,
    task_grading_overview,
    task_grading_summary,
    task_short_answer_snapshot_ids,
    task_uses_manual_short_answer_grading,
)
from apps.assessment.models import SubmissionEvent

from ..models import ClassExperimentTask
from .grading_serializers import (
    ManualGradeResponseSerializer,
    ManualGradeInputSerializer,
    ManualGradingOverviewSerializer,
    ManualGradingQuestionDetailSerializer,
)


class ManualGradingPagination(FixedPageNumberPagination):
    page_size = 50


class TeacherTaskGradingMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def get_task(self):
        task = get_object_or_404(
            ClassExperimentTask.objects.select_related(
                "experiment__course", "class_group", "release_snapshot"
            ),
            pk=self.kwargs["task_id"],
        )
        if not task.experiment.course.can_manage(self.request.user):
            raise Http404
        return task

    def get_manual_task(self):
        task = self.get_task()
        if not task_uses_manual_short_answer_grading(task):
            raise ApiConflict(
                "该任务未采用人工简答题评分。",
                code="NOT_MANUAL_SHORT_ANSWER",
            )
        return task


class TeacherClassTaskGradingOverviewView(TeacherTaskGradingMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_grading_overview",
        responses=ManualGradingOverviewSerializer,
    )
    def get(self, request, task_id):
        return Response(task_grading_overview(self.get_manual_task()))


class TeacherClassTaskGradingQuestionView(TeacherTaskGradingMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_grading_question",
        parameters=[
            OpenApiParameter(
                name="status",
                type=str,
                enum=["pending", "completed", "all"],
                default="pending",
            ),
            OpenApiParameter(name="page", type=int, default=1),
        ],
        responses=ManualGradingQuestionDetailSerializer,
    )
    def get(self, request, task_id, snapshot_id):
        task = self.get_manual_task()
        if snapshot_id not in task_short_answer_snapshot_ids(task):
            raise Http404
        snapshot = get_object_or_404(
            task.release_snapshot.question_snapshots,
            pk=snapshot_id,
            question_type="short_answer",
        )
        status_filter = request.query_params.get("status", "pending")
        if status_filter not in {"pending", "completed", "all"}:
            raise ApiBusinessError(
                "无效的批改状态筛选。",
                code="INVALID_GRADING_STATUS",
                status=400,
            )
        submissions = SubmissionEvent.objects.filter(
            attempt__session__class_task=task,
            attempt__assignment__snapshot=snapshot,
        ).select_related("attempt__session__student")
        if status_filter == "pending":
            submissions = submissions.exclude(
                grading_status=SubmissionEvent.GradingStatus.COMPLETED
            )
        elif status_filter == "completed":
            submissions = submissions.filter(
                grading_status=SubmissionEvent.GradingStatus.COMPLETED
            )
        submissions = submissions.order_by(
            "attempt__session__student__student_number",
            "attempt__session__student_id",
        )
        paginator = ManualGradingPagination()
        page = paginator.paginate_queryset(submissions, request, view=self)
        rows = [grading_submission_projection(item) for item in page]
        page_response = paginator.get_paginated_response(rows).data
        return Response(
            {
                "question": {
                    "question_snapshot_id": snapshot.pk,
                    "code": snapshot.code,
                    "text": snapshot.text,
                    "correct_answer": snapshot.correct_answer,
                    "accepted_answers": snapshot.accepted_answers,
                    "explanation": snapshot.explanation,
                },
                "submissions": page_response,
            }
        )


class TeacherClassTaskGradeSubmissionView(TeacherTaskGradingMixin, APIView):
    @extend_schema(
        operation_id="teacher_class_task_grade_submission",
        request=ManualGradeInputSerializer,
        responses={201: ManualGradeResponseSerializer},
    )
    def post(self, request, task_id, submission_id):
        task = self.get_task()
        serializer = ManualGradeInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            grade_task_short_answer(
                task_id=task.pk,
                submission_id=submission_id,
                actor=request.user,
                expected_grade_version=serializer.validated_data[
                    "expected_grade_version"
                ],
                is_correct=serializer.validated_data["is_correct"],
                comment=serializer.validated_data.get("comment", ""),
            )
        except ManualGradingError as exc:
            if exc.code == "SUBMISSION_SCOPE_MISMATCH":
                raise ApiBusinessError(str(exc), code=exc.code, status=404) from exc
            raise ApiConflict(str(exc), code=exc.code) from exc

        submission = (
            SubmissionEvent.objects.select_related("attempt__session__student")
            .prefetch_related("grade_records__grader")
            .get(pk=submission_id)
        )
        overview = task_grading_overview(task)
        snapshot_id = submission.attempt.assignment.snapshot_id
        question_progress = next(
            question
            for question in overview["questions"]
            if question["question_snapshot_id"] == snapshot_id
        )
        return Response(
            {
                "submission": grading_submission_projection(submission),
                "question_progress": question_progress,
                "task_progress": task_grading_summary(task),
            },
            status=201,
        )
