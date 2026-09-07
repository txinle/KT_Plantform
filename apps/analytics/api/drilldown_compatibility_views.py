from django.conf import settings
from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import get_accessible_course
from apps.accounts.models import User
from apps.assessment.models import ExperimentSession, StudentQuestionAssignment
from apps.experiments.models import ClassExperimentTask, Experiment
from apps.experiments.presentation import build_session_report_presentation

from .drilldown_compatibility_serializers import (
    TeacherExperimentResultsResolutionSerializer,
    TeacherExperimentSessionResolutionSerializer,
)
from .drilldown_views import _class_experiment_url, _course_payload


class TeacherExperimentCompatibilityApiView(APIView):
    """Resolve historical result URLs without asking Vue to infer task scope."""

    def _course_and_experiment(self, request, course_id, experiment_id):
        user = request.user
        if not (
            user.is_superuser
            or user.role in {User.Role.TEACHER, User.Role.ADMIN}
        ):
            raise NotFound()
        try:
            course = get_accessible_course(user, course_id)
        except (Http404, PermissionDenied) as error:
            raise NotFound() from error
        experiment = get_object_or_404(course.experiments, pk=experiment_id)
        return course, experiment

    @staticmethod
    def _experiment_payload(experiment):
        return {
            "id": experiment.pk,
            "name": experiment.name,
            "status": experiment.status,
        }


class TeacherExperimentResultsResolutionView(TeacherExperimentCompatibilityApiView):
    @extend_schema(
        parameters=[OpenApiParameter("status", str, required=False)],
        responses=TeacherExperimentResultsResolutionSerializer,
    )
    def get(self, request, course_id, experiment_id):
        course, experiment = self._course_and_experiment(
            request, course_id, experiment_id
        )
        results_url = reverse(
            "courses:experiment_results", args=[course.pk, experiment.pk]
        )
        payload = {
            "course": _course_payload(course),
            "experiment": self._experiment_payload(experiment),
            "canonical_url": None,
            "tasks": [],
            "sessions": [],
            "session_status_choices": [],
            "selected_status": "",
            "actions": {
                "back_url": reverse("courses:experiments", args=[course.pk]),
                "clear_filter_url": results_url,
            },
        }
        if settings.CLASS_CENTER_ENABLED:
            tasks = tuple(
                ClassExperimentTask.objects.filter(
                    experiment=experiment,
                    class_group__course=course,
                )
                .select_related("class_group", "class_snapshot")
                .order_by("class_group__academic_year", "class_group__name", "pk")
            )
            analysis_available = experiment.status in {
                Experiment.Status.FROZEN,
                Experiment.Status.PUBLISHED,
                Experiment.Status.CLOSED,
            }
            if analysis_available and len(tasks) == 1:
                task = tasks[0]
                payload.update(
                    {
                        "kind": "canonical",
                        "canonical_url": _class_experiment_url(
                            course,
                            task.class_group,
                            experiment.pk,
                        ),
                    }
                )
            else:
                payload.update(
                    {
                        "kind": "class_tasks",
                        "tasks": [
                            {
                                "academic_year": task.class_group.academic_year,
                                "class_name": task.class_group.name,
                                "class_snapshot_name": task.class_snapshot.display_name,
                                "analysis_url": _class_experiment_url(
                                    course,
                                    task.class_group,
                                    experiment.pk,
                                ),
                            }
                            for task in tasks
                        ]
                        if analysis_available
                        else [],
                    }
                )
            return Response(TeacherExperimentResultsResolutionSerializer(payload).data)

        latest_version = experiment.versions.order_by("-version_number").first()
        sessions = (
            latest_version.sessions.select_related("student")
            if latest_version
            else ExperimentSession.objects.none()
        )
        valid_statuses = {value for value, _label in ExperimentSession.Status.choices}
        selected_status = request.query_params.get("status", "")
        if selected_status in valid_statuses:
            sessions = sessions.filter(status=selected_status)
        else:
            selected_status = ""
        payload.update(
            {
                "kind": "sessions",
                "selected_status": selected_status,
                "session_status_choices": [
                    {"value": value, "label": label}
                    for value, label in ExperimentSession.Status.choices
                ],
                "sessions": [
                    {
                        "student_number": session.student.student_number,
                        "display_name": session.student.shown_name,
                        "status": session.status,
                        "status_label": session.get_status_display(),
                        "started_at": session.started_at,
                        "submission_count": session.submission_count,
                        "detail_url": reverse(
                            "courses:experiment_session_detail",
                            args=[course.pk, experiment.pk, session.pk],
                        ),
                    }
                    for session in sessions
                ],
            }
        )
        return Response(TeacherExperimentResultsResolutionSerializer(payload).data)


class TeacherExperimentSessionResolutionView(TeacherExperimentCompatibilityApiView):
    @extend_schema(responses=TeacherExperimentSessionResolutionSerializer)
    def get(self, request, course_id, experiment_id, session_id):
        course, experiment = self._course_and_experiment(
            request, course_id, experiment_id
        )
        session = get_object_or_404(
            ExperimentSession.objects.select_related(
                "student", "version", "class_snapshot"
            ),
            pk=session_id,
            version__experiment=experiment,
        )
        results_url = reverse(
            "courses:experiment_results", args=[course.pk, experiment.pk]
        )
        payload = {
            "course": _course_payload(course),
            "experiment": self._experiment_payload(experiment),
            "canonical_url": None,
            "report": None,
            "actions": {
                "back_url": results_url,
                "word_url": None,
            },
        }
        if settings.CLASS_CENTER_ENABLED:
            task = (
                ClassExperimentTask.objects.filter(
                    class_group__course=course,
                    experiment=experiment,
                    release_snapshot=session.version,
                    class_snapshot=session.class_snapshot,
                )
                .select_related("class_group")
                .first()
            )
            if task is not None:
                payload.update(
                    {
                        "kind": "canonical",
                        "canonical_url": reverse(
                            "courses:class_student_experiment_detail",
                            args=[
                                course.pk,
                                task.class_group_id,
                                session.student_id,
                                experiment.pk,
                            ],
                        ),
                    }
                )
                return Response(
                    TeacherExperimentSessionResolutionSerializer(payload).data
                )

        assignments = (
            StudentQuestionAssignment.objects.filter(session=session)
            .select_related("snapshot", "attempt")
            .order_by("assigned_sequence_index")
        )
        report = build_session_report_presentation(session, assignments)
        payload.update(
            {
                "kind": "legacy_report",
                "report": {
                    "student_name": report.student_name,
                    "student_number": report.student_number,
                    "class_snapshot_name": report.class_snapshot_name,
                    "version_number": report.version_number,
                    "session_status": report.session_status,
                    "question_count": report.question_count,
                    "questions": [
                        {
                            "assigned_sequence_index": question.assigned_sequence_index,
                            "stable_code": question.stable_code,
                            "question_type": question.question_type,
                            "question_type_label": question.question_type_label,
                            "text": question.text,
                            "options": [
                                {
                                    "key": option.key,
                                    "text": option.text,
                                    "display": option.display,
                                }
                                for option in question.options
                            ],
                            "student_answer": question.student_answer,
                            "correct_answer": question.correct_answer,
                            "answer_status": question.answer_status,
                        }
                        for question in report.questions
                    ],
                },
                "actions": {
                    "back_url": results_url,
                    "word_url": reverse(
                        "courses:experiment_session_word",
                        args=[course.pk, experiment.pk, session.pk],
                    ),
                },
            }
        )
        return Response(TeacherExperimentSessionResolutionSerializer(payload).data)
