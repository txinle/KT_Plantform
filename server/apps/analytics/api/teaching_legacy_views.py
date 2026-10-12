from urllib.parse import urlencode

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.analytics.request_scopes import (
    _teaching_page_context,
    _teaching_scope_display,
    _teaching_scope_from_request,
)

from .teaching_legacy_serializers import TeachingLegacyResponseSerializer
from .views import _course_for_teacher, _course_payload


def _raw_events_url(course, scope):
    from django.urls import reverse

    values = {**scope.serialize(), "table": "assessment_events"}
    return f"{reverse('courses:data_advanced', args=[course.pk])}?{urlencode(values)}"


class TeacherTeachingLegacyView(APIView):
    """Serialize the existing legacy teaching-workbench presentation facts."""

    @extend_schema(
        operation_id="teacher_legacy_teaching_retrieve",
        parameters=[
            OpenApiParameter("experiment_id", int, required=False),
            OpenApiParameter("class_id", int, required=False),
            OpenApiParameter("class_snapshot_id", int, required=False),
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("student_id", int, required=False),
            OpenApiParameter("session_id", int, required=False),
        ],
        responses=TeachingLegacyResponseSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        scope = _teaching_scope_from_request(request, course)
        context = _teaching_page_context(request, scope)
        payload = {
            "course": _course_payload(course),
            "scope": {
                "values": scope.serialize(),
                "experiment_id": scope.experiment_id,
                "class_id": scope.class_id,
                "is_complete": scope.is_complete,
                "display": _teaching_scope_display(scope),
            },
            "blocked_message": context["blocked_message"],
            "filters": {
                "q": context["student_search"],
                "student_id": context["selected_student_id"],
                "session_id": context["selected_session_id"],
            },
            "total_roster_count": context["total_roster_count"],
            "student_summaries": [
                {
                    "student_id": row["student"].pk,
                    "student_number": row["student"].student_number
                    or row["student"].username,
                    "display_name": row["student"].shown_name,
                    "data_status": str(row["data_status"]),
                    "data_status_label": row["data_status_label"],
                    "attempt_count": row["attempt_count"],
                    "submitted_count": row["submitted_count"],
                    "graded_count": row["graded_count"],
                    "correct_rate": row["correct_rate"],
                    "score": row["score"],
                    "max_score": row["max_score"],
                }
                for row in context["student_summaries"]
            ],
            "sessions": [
                {
                    "id": row["session"].pk,
                    "status": row["session"].status,
                    "status_label": row["session"].get_status_display(),
                    "attempt_count": row["attempt_count"],
                    "submitted_count": row["submitted_count"],
                    "started_at": row["session"].started_at,
                }
                for row in context["session_rows"]
            ],
            "attempts": [
                {
                    "id": row["attempt"].pk,
                    "question_code": row["attempt"].assignment.snapshot.code,
                    "status": row["attempt"].status,
                    "status_label": row["attempt"].get_status_display(),
                    "grade_score": row["grade"].score if row["grade"] else None,
                    "timing": {
                        "display_duration_ms": row["timing"].display_duration_ms,
                        "display_label": row["timing"].display_label,
                    },
                    "timeline": [
                        {
                            "event_id": event["event_id"],
                            "event_type": event["event_type"],
                            "occurred_at": event["occurred_at"],
                            "interval_quality": event["interval_quality"],
                        }
                        for event in row["timeline"]
                    ],
                    "raw_events_url": _raw_events_url(course, scope),
                }
                for row in context["attempt_rows"]
            ],
        }
        return Response(TeachingLegacyResponseSerializer(payload).data)
