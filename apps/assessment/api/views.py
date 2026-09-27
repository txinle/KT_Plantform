import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db.models import F, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import parse_etags
from drf_spectacular.utils import extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Course, CourseEnrollment, User
from apps.api.exceptions import ApiConflict
from apps.api.permissions import RolePermission
from apps.experiments.models import (
    ClassExperimentTask,
    ExperimentVersionParticipantSnapshot,
    PublishPreviewParticipant,
    ScheduleRevision,
)
from apps.experiments.presentation import options_for_snapshot

from ..confidence_services import confidence_prompt_contract
from ..deadline_services import DeadlineDecisionService
from ..models import (
    Attempt,
    ConfidenceAssignment,
    ExperimentSession,
    StudentQuestionAssignment,
)
from ..response_snapshot_services import canonical_response_state
from ..services import can_recover_session, get_or_create_session, resume_task_session
from ..services import record_visit_entry
from ..student_results import build_student_task_result
from .serializers import (
    StudentCourseCollectionSerializer,
    StudentLearningExperimentSerializer,
    StudentLearningProjectionSerializer,
    StudentLearningQuestionSerializer,
    StudentSessionEntrySerializer,
    StudentRuntimeQuestionEntryQuerySerializer,
    StudentRuntimeQuestionEntrySerializer,
    StudentResultResponseSerializer,
    StudentTaskCollectionSerializer,
    serialize_student_result,
)


def _course_projection(enrollment):
    return {
        "course_id": enrollment.course_id,
        "course_name": enrollment.course.name,
        "course_code": enrollment.course.code,
        "course_term": enrollment.course.term,
        "course_status": enrollment.course.status,
        "class_name": enrollment.class_group.name,
    }


def _student_enrollment(student, course_id):
    return get_object_or_404(
        CourseEnrollment.objects.select_related("course", "class_group"),
        course_id=course_id,
        student=student,
        is_active=True,
    )


def _student_task_queryset(student, *, course_id=None):
    queryset = (
        ClassExperimentTask.objects.filter(
            participant_snapshots__student=student,
            participant_snapshots__version_id=F("release_snapshot_id"),
            participant_snapshots__class_snapshot_id=F("class_snapshot_id"),
        )
        .select_related(
            "experiment",
            "class_group",
            "class_snapshot",
            "release_snapshot",
            "preview_class_result",
        )
        .prefetch_related(
            Prefetch(
                "schedule_revisions",
                queryset=ScheduleRevision.objects.order_by("-revision_no"),
                to_attr="latest_schedule_revisions",
            ),
            Prefetch(
                "preview_class_result__participants",
                queryset=PublishPreviewParticipant.objects.filter(student=student),
                to_attr="current_student_preview_participants",
            ),
            Prefetch(
                "release_snapshot__question_snapshots",
                to_attr="all_question_snapshots",
            ),
            Prefetch(
                "participant_snapshots",
                queryset=ExperimentVersionParticipantSnapshot.objects.filter(
                    student=student
                ),
                to_attr="current_student_participants",
            ),
            Prefetch(
                "sessions",
                queryset=ExperimentSession.objects.filter(student=student).prefetch_related(
                    "attempts"
                ),
                to_attr="current_student_sessions",
            ),
        )
        .order_by("-published_at", "-pk")
        .distinct()
    )
    if course_id is not None:
        queryset = queryset.filter(experiment__course_id=course_id)
    return queryset


def _task_projection(task, *, course):
    schedule = next(iter(task.latest_schedule_revisions), None)
    session = next(iter(task.current_student_sessions), None)
    if session is not None:
        attempts = list(session.attempts.all())
        total = len(attempts)
        completed = sum(
            attempt.status
            in {
                Attempt.Status.SUBMITTED,
                Attempt.Status.TIMED_OUT,
                Attempt.Status.UNANSWERED,
            }
            for attempt in attempts
        )
    else:
        participant = next(iter(task.current_student_participants), None)
        preview = None
        if task.preview_class_result_id and participant is not None:
            preview = next(
                iter(task.preview_class_result.current_student_preview_participants),
                None,
            )
        total = (
            preview.item_count
            if preview is not None
            else len(task.release_snapshot.all_question_snapshots)
        )
        completed = 0

    now = timezone.now()
    business_status = DeadlineDecisionService.get_task_state(task, at=now)
    primary_action = None
    available_actions = []
    if session is None:
        if course.status == Course.Status.ACTIVE and business_status == "open":
            primary_action = "start"
            available_actions = ["start"]
    elif session.status == ExperimentSession.Status.IN_PROGRESS:
        decision = DeadlineDecisionService.can_enter_item(task, session, now)
        if course.status == Course.Status.ACTIVE and (
            decision.allowed or can_recover_session(task, session, at=now)
        ):
            primary_action = "resume"
            available_actions = ["resume"]
    elif course.status == Course.Status.ACTIVE and can_recover_session(task, session, at=now):
        primary_action = "resume"
        available_actions = ["resume", "view_result"]
    else:
        primary_action = "view_result"
        available_actions = ["view_result"]
    return {
        "task_id": task.pk,
        "experiment_id": task.experiment_id,
        "experiment_name": task.experiment.name,
        "class_name": task.class_snapshot.display_name,
        "business_status": business_status,
        "opens_at": schedule.opens_at if schedule is not None else None,
        "closes_at": schedule.closes_at if schedule is not None else None,
        "progress": {"completed": completed, "total": total},
        "primary_action": primary_action,
        "available_actions": available_actions,
        "feedback_mode": task.release_snapshot.configuration.get(
            "feedback_mode", "none"
        ),
    }


def _student_task(student, task_id):
    return get_object_or_404(_student_task_queryset(student), pk=task_id)


def _entry_projection(task, session):
    assignment = session.current_assignment or session.assignments.first()
    if assignment is None:
        raise ApiConflict("正式任务没有可作答题目。", code="TASK_EMPTY")
    return {
        "task_id": task.pk,
        "session_id": session.pk,
        "question_url": reverse(
            "assessment:question",
            args=[session.pk, assignment.pk],
        ),
    }


class StudentApiMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.STUDENT,)


class StudentCourseCollectionView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentCourseCollectionSerializer)
    def get(self, request):
        enrollments = CourseEnrollment.objects.filter(
            student=request.user,
            is_active=True,
        ).select_related("course", "class_group")
        return Response(
            {"items": [_course_projection(enrollment) for enrollment in enrollments]}
        )


class StudentTaskCollectionView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentTaskCollectionSerializer)
    def get(self, request, course_id):
        enrollment = _student_enrollment(request.user, course_id)
        return Response(
            {
                "course": _course_projection(enrollment),
                "items": [
                    _task_projection(task, course=enrollment.course)
                    for task in _student_task_queryset(
                        request.user,
                        course_id=course_id,
                    )
                ],
            }
        )


class StudentTaskStartView(StudentApiMixin, APIView):
    @extend_schema(request=None, responses={200: StudentSessionEntrySerializer, 201: StudentSessionEntrySerializer})
    def post(self, request, task_id):
        task = _student_task(request.user, task_id)
        existing = ExperimentSession.objects.filter(
            class_task=task,
            participant_snapshot__student=request.user,
        ).first()
        try:
            session = get_or_create_session(
                task.experiment,
                request.user,
                request_received_at=timezone.now(),
                class_task=task,
            )
        except ValidationError as error:
            raise ApiConflict("; ".join(error.messages), code="TASK_NOT_OPEN") from error
        return Response(
            _entry_projection(task, session),
            status=200 if existing is not None else 201,
        )


class StudentTaskResumeView(StudentApiMixin, APIView):
    @extend_schema(request=None, responses=StudentSessionEntrySerializer)
    def post(self, request, task_id):
        task = _student_task(request.user, task_id)
        try:
            session = resume_task_session(
                task,
                request.user,
                request_received_at=timezone.now(),
            )
        except ExperimentSession.DoesNotExist as error:
            raise ApiConflict(
                "该任务尚未开始。",
                code="SESSION_NOT_STARTED",
            ) from error
        except ValidationError as error:
            raise ApiConflict(
                "; ".join(error.messages),
                code="SESSION_NOT_RESUMABLE",
            ) from error
        return Response(_entry_projection(task, session))


def _runtime_remaining_seconds(session, attempt, at):
    item_deadline = attempt.current_effective_deadline_at or attempt.initial_effective_deadline_at
    task_close = None
    if session.class_task_id:
        schedule = DeadlineDecisionService.schedule_at(session.class_task, at)
        task_close = schedule.closes_at if schedule is not None else None
    deadline = min(
        (value for value in (item_deadline, session.deadline_at, task_close) if value is not None),
        default=None,
    )
    if deadline:
        return max(
            0,
            int((deadline - at).total_seconds()),
        )
    config = session.version.configuration
    if config.get("time_mode") == "per_question":
        return max(
            0,
            int(attempt.per_item_time_limit_seconds or config.get("per_question_time_limit_seconds") or 0)
            - (attempt.active_seconds - attempt.active_seconds_at_recovery),
        )
    if config.get("time_mode") == "total" and session.deadline_at:
        return max(0, int((session.deadline_at - at).total_seconds()))
    return None


def _runtime_initial_response_state(attempt):
    question_type = attempt.assignment.snapshot.question_type
    if question_type == "short_answer" and attempt.saved_response_snapshot is not None:
        saved = attempt.saved_response_snapshot
        return {
            "snapshot_id": str(saved.pk),
            "state_hash": saved.state_hash,
            "content_hash": saved.content_hash,
            "content_length": saved.content_length,
            "schema_version": saved.schema_version,
        }
    if attempt.draft_answer is not None:
        return canonical_response_state(question_type, attempt.draft_answer)
    return None


def _runtime_pending_confidence(attempt, confidence):
    if confidence is None:
        return None
    return {
        **confidence_prompt_contract(confidence),
        "prompt_shown_url": reverse(
            "assessment_process:confidence_prompt_shown", args=[attempt.pk]
        ),
        "commit_url": reverse(
            "assessment_process:confidence_commit", args=[attempt.pk]
        ),
        "skip_url": reverse(
            "assessment_process:confidence_skip", args=[attempt.pk]
        ),
    }


def _runtime_options(assignment):
    if assignment.snapshot.question_type == "true_false":
        return [
            {"key": "true", "text": "正确"},
            {"key": "false", "text": "错误"},
        ]
    options = [
        {"key": option.key, "text": option.text}
        for option in options_for_snapshot(assignment.snapshot)
    ]
    order = list(assignment.item_instance.option_order or [])
    if not order:
        return options
    positions = {key: index for index, key in enumerate(order)}
    return sorted(options, key=lambda option: positions.get(option["key"], len(order)))


class StudentRuntimeQuestionEntryView(StudentApiMixin, APIView):
    @extend_schema(
        parameters=[StudentRuntimeQuestionEntryQuerySerializer],
        responses=StudentRuntimeQuestionEntrySerializer,
    )
    def get(self, request, session_id, assignment_id):
        request_received_at = timezone.now()
        query = StudentRuntimeQuestionEntryQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        session = get_object_or_404(
            ExperimentSession.objects.select_related(
                "version__experiment",
                "class_task",
            ),
            pk=session_id,
            student=request.user,
        )
        assignment = get_object_or_404(
            StudentQuestionAssignment.objects.select_related(
                "snapshot",
                "item_instance",
                "attempt__current_response_snapshot",
            ),
            pk=assignment_id,
            session=session,
        )
        try:
            visit_entry = record_visit_entry(
                session.pk,
                assignment.pk,
                query.validated_data["reason"],
                query.validated_data.get("client_occurred_at"),
                request_received_at=request_received_at,
                navigation_id=query.validated_data.get("navigation_id"),
                client_monotonic_ms=query.validated_data.get("client_monotonic_ms"),
                client_time_origin=query.validated_data.get("client_time_origin"),
            )
        except ValidationError as error:
            raise ApiConflict(
                "; ".join(error.messages),
                code="SESSION_NOT_ENTERABLE",
            ) from error
        attempt = (
            Attempt.objects.select_related(
                "assignment__snapshot",
                "assignment__item_instance",
                "current_response_snapshot",
            )
            .get(pk=visit_entry.attempt.pk)
        )
        assignment = attempt.assignment
        session.refresh_from_db()
        assignments = list(
            session.assignments.select_related("attempt").order_by(
                "assigned_sequence_index"
            )
        )
        from ..research_tool_services import available_tools, tool_configuration
        research_tools = available_tools(attempt)
        payload = {
            'research_tools': research_tools,
            "task_id": session.class_task_id,
            "session_id": session.pk,
            "session_status": session.status,
            "experiment_name": session.version.experiment.name,
            "assignment": {
                "assignment_id": assignment.pk,
                "attempt_id": attempt.pk,
                "status": attempt.status,
                "sequence_index": assignment.assigned_sequence_index,
            },
            "progress": {
                "submitted": session.submission_count,
                "total": len(assignments),
            },
            "remaining_seconds": _runtime_remaining_seconds(
                session,
                attempt,
                request_received_at,
            ),
            "question": {
                "snapshot_id": assignment.snapshot_id,
                "stable_code": assignment.snapshot.stable_code,
                "code": assignment.snapshot.code,
                "question_type": assignment.snapshot.question_type,
                "text": assignment.snapshot.text,
                "primary_kc": assignment.snapshot.primary_kc if research_tools['kc_visible'] else '',
                "options": _runtime_options(assignment),
            },
            "draft_answer": attempt.draft_answer,
            "initial_response_state": _runtime_initial_response_state(attempt),
            "pending_confidence": _runtime_pending_confidence(
                attempt,
                ConfidenceAssignment.objects.filter(attempt=attempt, condition=ConfidenceAssignment.Condition.PROMPT).first() if attempt.status != Attempt.Status.SUBMITTED else None,
            ),
            "navigation": [
                {
                    "assignment_id": item.pk,
                    "sequence_index": item.assigned_sequence_index,
                    "status": item.attempt.status,
                    "url": reverse(
                        "assessment:question",
                        args=[session.pk, item.pk],
                    ),
                    "current": item.pk == assignment.pk,
                }
                for item in assignments
            ],
            "actions": {
                "research_tools_url": reverse('assessment_process:research_tools', args=[attempt.pk]),
                "resolve_branch_url": reverse('assessment_process:resolve_branch', args=[attempt.pk]),
                "save_draft_url": reverse("assessment:save_draft", args=[attempt.pk]),
                "submit_url": reverse("assessment:submit", args=[attempt.pk]),
                "skip_url": reverse("assessment:skip", args=[attempt.pk]),
                "finish_url": reverse("assessment:finish", args=[session.pk]),
            },
            "process": {
                "visit_id": visit_entry.visit.pk,
                "visit_index": visit_entry.visit.visit_index,
                "entry_event_id": str(visit_entry.entry_event.pk),
                "navigation_id": str(visit_entry.navigation_id),
                "writer_lease_acquire_url": reverse('assessment_process:writer_lease_acquire', args=[attempt.pk]),
                "writer_lease_renew_url": reverse('assessment_process:writer_lease_renew', args=[attempt.pk]),
                "writer_lease_release_url": reverse('assessment_process:writer_lease_release', args=[attempt.pk]),
                "capture_version": session.capture_contract.contract_name if session.capture_contract_id else 'legacy',
                "active_response_event_id": str(attempt.active_response_event_id) if attempt.active_response_event_id else None,
                "response_version": attempt.response_version,
                "session_id": session.pk,
                "attempt_id": attempt.pk,
                "event_batch_url": reverse("assessment_process:capture_v2_batch" if session.capture_contract_id and session.capture_contract.contract_name == 'capture-v2' else "assessment_process:event_batch"),
                "checkpoint_url": reverse(
                    "assessment_process:capture_checkpoint"
                ),
                "time_sync_create_url": reverse(
                    "assessment_process:time_sync_create"
                ),
                "time_sync_finalize_url": reverse(
                    "assessment_process:time_sync_finalize"
                ),
                "app_version": "2026.08.29.2",
                "heartbeat_interval_seconds": session.version.configuration.get(
                    "heartbeat_interval_seconds", 30
                ),
            },
        }
        if not session.capture_contract_id or session.capture_contract.contract_name != 'capture-v2':
            payload.pop('research_tools', None)
            payload['actions'].pop('research_tools_url', None)
            payload['actions'].pop('resolve_branch_url', None)
            for key in ('capture_version', 'active_response_event_id', 'response_version', 'writer_lease_acquire_url', 'writer_lease_renew_url', 'writer_lease_release_url'):
                payload['process'].pop(key, None)
        else:
            tool_config = tool_configuration(attempt)
            if not any((
                tool_config.get('doubt_enabled'),
                tool_config.get('check_enabled'),
                tool_config.get('hints'),
                tool_config.get('rules'),
            )):
                payload['actions'].pop('research_tools_url', None)
            from datetime import timedelta
            remaining = payload['remaining_seconds']
            payload['presented_deadline_at'] = request_received_at + timedelta(seconds=remaining) if remaining is not None else None
        response_payload = StudentRuntimeQuestionEntrySerializer(payload).data
        if "presented_deadline_at" not in payload:
            response_payload.pop("presented_deadline_at", None)
        response = Response(response_payload)
        if payload.get('research_tools') is not None:
            response['X-Capture-Contract'] = 'capture-v2'
        return response


class StudentTaskResultView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentResultResponseSerializer)
    def get(self, request, task_id):
        task = _student_task(request.user, task_id)
        return Response(
            serialize_student_result(build_student_task_result(task, request.user))
        )


def _learning_enrollment(student, course_id):
    if not settings.STUDENT_LEARNING_ANALYTICS_ENABLED:
        raise Http404
    return _student_enrollment(student, course_id)


class StudentLearningView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentLearningProjectionSerializer)
    def get(self, request, course_id):
        from apps.analytics.teaching.student_projection import (
            build_student_learning_projection,
            student_projection_to_api,
        )

        enrollment = _learning_enrollment(request.user, course_id)
        try:
            projection = build_student_learning_projection(enrollment)
        except LookupError as error:
            raise Http404 from error
        payload = student_projection_to_api(projection)
        return Response(StudentLearningProjectionSerializer(instance=payload).data)


class StudentLearningExperimentView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentLearningExperimentSerializer)
    def get(self, request, course_id, experiment_id):
        enrollment = _learning_enrollment(request.user, course_id)
        try:
            from apps.analytics.student_result_snapshots import (
                get_student_experiment_result_payload,
            )

            payload = get_student_experiment_result_payload(
                enrollment,
                experiment_id=experiment_id,
            )
        except LookupError as error:
            raise Http404 from error
        # PostgreSQL jsonb may reorder object keys without changing the result.
        payload_hash = hashlib.sha256(json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        ).encode()).hexdigest()
        etag = f'W/"student-result-{payload_hash}"'
        response_headers = {
            "ETag": etag,
            "Cache-Control": "private, no-cache",
        }
        requested_etags = parse_etags(request.headers.get("If-None-Match", ""))
        if "*" in requested_etags or etag.removeprefix("W/") in [tag.removeprefix("W/") for tag in requested_etags]:
            return Response(status=304, headers=response_headers)
        return Response(payload, headers=response_headers)


class StudentLearningQuestionView(StudentApiMixin, APIView):
    @extend_schema(responses=StudentLearningQuestionSerializer)
    def get(self, request, course_id, experiment_id, question_id):
        from apps.analytics.teaching.student_projection import (
            build_student_question_projection,
            student_projection_to_api,
        )

        enrollment = _learning_enrollment(request.user, course_id)
        try:
            projection = build_student_question_projection(
                enrollment,
                experiment_id=experiment_id,
                question_id=question_id,
            )
        except LookupError as error:
            raise Http404 from error
        payload = student_projection_to_api(projection)
        return Response(StudentLearningQuestionSerializer(instance=payload).data)
