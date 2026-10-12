from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.class_scope import _class_drilldown_scope
from apps.accounts.models import User

from ..teaching.drilldown import (
    build_attempt_process_detail,
    build_question_detail,
    build_student_detail,
    build_student_experiment_detail,
)
from .drilldown_serializers import (
    AttemptProcessResponseSerializer,
    QuestionDetailResponseSerializer,
    StudentDetailResponseSerializer,
    StudentExperimentDetailResponseSerializer,
)


def _metric_payload(metric):
    return {
        "numerator": metric.numerator,
        "denominator": metric.denominator,
        "ratio": metric.ratio,
        "state": metric.state,
        "sample_size": metric.sample_size,
        "percent_display": metric.percent_display,
    }


def _course_payload(course):
    return {
        "id": course.pk,
        "name": course.name,
        "code": course.code,
        "term": course.term,
        "status": course.status,
    }


def _class_group_payload(class_group):
    return {
        "id": class_group.pk,
        "name": class_group.name,
        "academic_year": class_group.academic_year,
    }


def _class_experiment_url(course, class_group, experiment_id):
    return (
        f"{reverse('courses:class_detail', args=[course.pk, class_group.pk])}"
        f"?section=experiments&experiment_id={experiment_id}"
    )


class TeacherDrilldownApiView(APIView):
    """Serve teacher-only frozen fact projections for class drilldown pages."""

    def _scope(self, request, course_id, class_id):
        user = request.user
        if not (
            user.is_superuser
            or user.role in {User.Role.TEACHER, User.Role.ADMIN}
        ):
            raise NotFound()
        try:
            return _class_drilldown_scope(request, course_id, class_id)
        except (Http404, PermissionDenied) as error:
            raise NotFound() from error

    @staticmethod
    def _not_found_from_lookup(callback):
        try:
            return callback()
        except LookupError as error:
            raise NotFound() from error

    @staticmethod
    def _base_payload(course, class_group):
        return {
            "course": _course_payload(course),
            "class_group": _class_group_payload(class_group),
        }


class TeacherStudentDetailView(TeacherDrilldownApiView):
    @extend_schema(responses=StudentDetailResponseSerializer)
    def get(self, request, course_id, class_id, student_id):
        course, class_group = self._scope(request, course_id, class_id)
        detail = self._not_found_from_lookup(
            lambda: build_student_detail(class_group, student_id=student_id)
        )
        payload = self._base_payload(course, class_group)
        payload["detail"] = {
            "generated_at": detail.generated_at,
            "student_id": detail.student_id,
            "student_number": detail.student_number,
            "display_name": detail.display_name,
            "completed_experiments": detail.completed_experiments,
            "eligible_experiments": detail.eligible_experiments,
            "correct_rate": _metric_payload(detail.correct_rate),
            "score_rate": _metric_payload(detail.score_rate),
            "average_answer_time_ms": detail.average_answer_time_ms,
            "average_answer_time_display": detail.average_answer_time_display,
            "experiments": [
                {
                    "experiment_id": row.experiment_id,
                    "name": row.name,
                    "completed": row.completed,
                    "correct_rate": _metric_payload(row.correct_rate),
                    "score_rate": _metric_payload(row.score_rate),
                    "rank": row.rank,
                    "rank_sample_size": row.rank_sample_size,
                    "average_answer_time_ms": row.average_answer_time_ms,
                    "average_answer_time_display": row.average_answer_time_display,
                    "detail_url": reverse(
                        "courses:class_student_experiment_detail",
                        args=[
                            course.pk,
                            class_group.pk,
                            detail.student_id,
                            row.experiment_id,
                        ],
                    ),
                }
                for row in detail.experiments
            ],
            "trend_rows": [
                {
                    "experiment_id": row.experiment_id,
                    "name": row.name,
                    "student_correct_rate": _metric_payload(
                        row.student_correct_rate
                    ),
                    "class_average_correct_rate": _metric_payload(
                        row.class_average_correct_rate
                    ),
                    "x": row.x,
                    "student_y": row.student_y,
                    "class_y": row.class_y,
                }
                for row in detail.trend_rows
            ],
            "show_trend_chart": detail.show_trend_chart,
            "kc_rows": [
                {
                    "name": row.name,
                    "comparison": {
                        "student_value": _metric_payload(
                            row.comparison.student_value
                        ),
                        "class_average": _metric_payload(
                            row.comparison.class_average
                        ),
                        "sample_size": row.comparison.sample_size,
                    },
                }
                for row in detail.kc_rows
            ],
        }
        payload["actions"] = {
            "back_url": (
                f"{reverse('courses:class_detail', args=[course.pk, class_group.pk])}"
                "?section=students"
            ),
        }
        return Response(StudentDetailResponseSerializer(payload).data)


class TeacherStudentExperimentDetailView(TeacherDrilldownApiView):
    @extend_schema(responses=StudentExperimentDetailResponseSerializer)
    def get(self, request, course_id, class_id, student_id, experiment_id):
        course, class_group = self._scope(request, course_id, class_id)
        detail = self._not_found_from_lookup(
            lambda: build_student_experiment_detail(
                class_group,
                experiment_id=experiment_id,
                student_id=student_id,
            )
        )
        payload = self._base_payload(course, class_group)
        payload["detail"] = {
            "generated_at": detail.generated_at,
            "experiment_id": detail.experiment_id,
            "experiment_name": detail.experiment_name,
            "student_id": detail.student_id,
            "student_number": detail.student_number,
            "display_name": detail.display_name,
            "is_current_member": detail.is_current_member,
            "completed": detail.completed,
            "correct_rate": _metric_payload(detail.correct_rate),
            "score_rate": _metric_payload(detail.score_rate),
            "average_answer_time_ms": detail.average_answer_time_ms,
            "average_answer_time_display": detail.average_answer_time_display,
            "rank": detail.rank,
            "rank_sample_size": detail.rank_sample_size,
            "questions": [
                {
                    "question_id": row.question_id,
                    "code": row.code,
                    "question_summary": row.question_summary,
                    "kcs": list(row.kcs),
                    "result_label": row.result_label,
                    "score_display": row.score_display,
                    "first_answer": row.first_answer,
                    "final_answer": row.final_answer,
                    "revision_count": row.revision_count,
                    "answer_time_ms": row.answer_time_ms,
                    "answer_time_display": row.answer_time_display,
                    "process_complete": row.process_complete,
                    "confidence_label": row.confidence_label,
                    "submission_status_label": row.submission_status_label,
                    "submitted_at": row.submitted_at,
                    "question_url": reverse(
                        "courses:class_question_detail",
                        args=[
                            course.pk,
                            class_group.pk,
                            detail.experiment_id,
                            row.question_id,
                        ],
                    ),
                    "process_url": reverse(
                        "courses:class_question_process",
                        args=[
                            course.pk,
                            class_group.pk,
                            detail.experiment_id,
                            row.question_id,
                            detail.student_id,
                        ],
                    ),
                }
                for row in detail.questions
            ],
        }
        payload["actions"] = {
            "back_url": _class_experiment_url(
                course,
                class_group,
                detail.experiment_id,
            ),
        }
        return Response(StudentExperimentDetailResponseSerializer(payload).data)


class TeacherQuestionDetailView(TeacherDrilldownApiView):
    @extend_schema(responses=QuestionDetailResponseSerializer)
    def get(self, request, course_id, class_id, experiment_id, question_id):
        course, class_group = self._scope(request, course_id, class_id)
        detail = self._not_found_from_lookup(
            lambda: build_question_detail(
                class_group,
                experiment_id=experiment_id,
                question_id=question_id,
            )
        )
        payload = self._base_payload(course, class_group)
        payload["detail"] = {
            "generated_at": detail.generated_at,
            "experiment_id": detail.experiment_id,
            "experiment_name": detail.experiment_name,
            "question_id": detail.question_id,
            "code": detail.code,
            "text": detail.text,
            "question_type": detail.question_type,
            "kcs": list(detail.kcs),
            "eligible_count": detail.eligible_count,
            "current_member_count": detail.current_member_count,
            "historical_participant_count": detail.historical_participant_count,
            "answer_count": detail.answer_count,
            "correct_count": detail.correct_count,
            "incorrect_count": detail.incorrect_count,
            "correct_rate": _metric_payload(detail.correct_rate),
            "timing": {
                "median_display": detail.timing.median_display,
                "iqr_display": detail.timing.iqr_display,
                "sample_size": detail.timing.sample_size,
                "quality": {
                    "submitted_count": detail.timing.quality.submitted_count,
                    "exact_count": detail.timing.quality.exact_count,
                    "estimated_count": detail.timing.quality.estimated_count,
                    "wall_clock_count": detail.timing.quality.wall_clock_count,
                    "unavailable_count": detail.timing.quality.unavailable_count,
                },
            },
            "chain_coverage": _metric_payload(detail.chain_coverage),
            "option_rows": [
                {
                    "key": row.key,
                    "label": row.label,
                    "count": row.count,
                    "is_correct": row.is_correct,
                }
                for row in detail.option_rows
            ],
            "transition_applicability": detail.transition_applicability,
            "transition_rows": [
                {"key": row.key, "label": row.label, "count": row.count}
                for row in detail.transition_rows
            ],
            "participants": [
                {
                    "student_id": row.student_id,
                    "student_number": row.student_number,
                    "display_name": row.display_name,
                    "is_current_member": row.is_current_member,
                    "result_label": row.result_label,
                    "final_answer": row.final_answer,
                    "process_complete": row.process_complete,
                    "student_experiment_url": reverse(
                        "courses:class_student_experiment_detail",
                        args=[
                            course.pk,
                            class_group.pk,
                            row.student_id,
                            detail.experiment_id,
                        ],
                    ),
                    "process_url": reverse(
                        "courses:class_question_process",
                        args=[
                            course.pk,
                            class_group.pk,
                            detail.experiment_id,
                            detail.question_id,
                            row.student_id,
                        ],
                    ),
                }
                for row in detail.participants
            ],
        }
        payload["actions"] = {
            "back_url": _class_experiment_url(
                course,
                class_group,
                detail.experiment_id,
            ),
        }
        return Response(QuestionDetailResponseSerializer(payload).data)


class TeacherAttemptProcessDetailView(TeacherDrilldownApiView):
    @extend_schema(responses=AttemptProcessResponseSerializer)
    def get(
        self,
        request,
        course_id,
        class_id,
        experiment_id,
        question_id,
        student_id,
    ):
        course, class_group = self._scope(request, course_id, class_id)
        detail = self._not_found_from_lookup(
            lambda: build_attempt_process_detail(
                class_group,
                experiment_id=experiment_id,
                question_id=question_id,
                student_id=student_id,
            )
        )
        payload = self._base_payload(course, class_group)
        payload["detail"] = {
            "generated_at": detail.generated_at,
            "experiment_id": detail.experiment_id,
            "experiment_name": detail.experiment_name,
            "question_id": detail.question_id,
            "question_code": detail.question_code,
            "question_text": detail.question_text,
            "question_options": list(detail.question_options),
            "correct_answer": detail.correct_answer,
            "student_id": detail.student_id,
            "student_number": detail.student_number,
            "display_name": detail.display_name,
            "is_current_member": detail.is_current_member,
            "result_label": detail.result_label,
            "score_display": detail.score_display,
            "confidence_label": detail.confidence_label,
            "answer_time_display": detail.answer_time_display,
            "process": {
                "question_type": detail.process.question_type,
                "completeness": detail.process.completeness,
                "completeness_label": detail.process.completeness_label,
                "steps": [
                    {
                        "label": step.label,
                        "occurred_at": step.occurred_at,
                        "value": step.value,
                        "value_before": step.value_before,
                        "value_after": step.value_after,
                        "length_before": step.length_before,
                        "length_after": step.length_after,
                        "time_source": step.time_source,
                        "interval_ms": step.interval_ms,
                    }
                    for step in detail.process.steps
                ],
                "first_value": detail.process.first_value,
                "final_value": detail.process.final_value,
                "revision_count": detail.process.revision_count,
            },
            "coverage_status_title": detail.coverage_status_title,
            "coverage_status_description": detail.coverage_status_description,
            "coverage_time_title": detail.coverage_time_title,
            "coverage_time_description": detail.coverage_time_description,
            "coverage_reason_labels": list(detail.coverage_reason_labels),
            "has_cross_instance_order_uncertainty": (
                detail.has_cross_instance_order_uncertainty
            ),
        }
        payload["actions"] = {
            "back_url": reverse(
                "courses:class_question_detail",
                args=[
                    course.pk,
                    class_group.pk,
                    detail.experiment_id,
                    detail.question_id,
                ],
            ),
        }
        return Response(AttemptProcessResponseSerializer(payload).data)
