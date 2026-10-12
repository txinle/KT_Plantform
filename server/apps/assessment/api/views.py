import hashlib
import json

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import F, Prefetch
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import parse_etags
from drf_spectacular.utils import extend_schema, OpenApiParameter, PolymorphicProxySerializer
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
from ..deadline_services import DeadlineDecision, DeadlineDecisionService
from ..models import (
    Attempt,
    AssessmentEvent,
    ConfidenceAssignment,
    ExperimentSession,
    StudentQuestionAssignment,
    QuestionVisitEvent,
)
from ..response_snapshot_services import canonical_response_state
from ..services import can_recover_session, get_or_create_session, resume_task_session
from ..services import RecordedVisitEntry, _validate_course_access, record_visit_entry
from ..admission import (
    AdmissionCoordinator,
    AdmissionInvalid,
    AdmissionUnavailable,
    admission_result_payload,
)
from ..student_results import build_student_task_result
from .serializers import (
    StudentCourseCollectionSerializer,
    StudentLearningExperimentSerializer,
    StudentLearningProjectionSerializer,
    StudentLearningQuestionSerializer,
    StudentProjectionStatusSerializer,
    StudentSessionEntrySerializer,
    StudentRuntimeQuestionEntrySerializer,
    StudentRuntimeQuestionEnterSerializer,
    StudentAdmissionStatusSerializer,
    StudentAdmissionStatusResponseSerializer,
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
    # Entry/result requests need the authorized task, not task-list projections.
    tasks = _student_task_queryset(student).prefetch_related(None).select_related(None)
    return get_object_or_404(
        tasks.select_related("experiment", "class_group", "class_snapshot", "release_snapshot"),
        pk=task_id,
    )


def _entry_projection(task, session, *, admission_token=None, admission_action=None):
    assignment = session.current_assignment or session.assignments.first()
    if assignment is None:
        raise ApiConflict("正式任务没有可作答题目。", code="TASK_EMPTY")
    payload = {
        "task_id": task.pk,
        "session_id": session.pk,
        "question_url": reverse(
            "assessment:question",
            args=[session.pk, assignment.pk],
        ),
    }
    if admission_token:
        payload["admission_token"] = admission_token
        payload["admission_action"] = admission_action
    return payload


def _admission_poll_url(task_id):
    return reverse("student-task-admission-status", args=[task_id])


def _admission_gate(request, task, action):
    if not AdmissionCoordinator.enabled_for(task.pk):
        return None
    coordinator = AdmissionCoordinator()
    token = str(request.data.get("admission_token") or "")
    try:
        if token:
            result = coordinator.status(
                task_id=task.pk,
                user_id=request.user.pk,
                action=action,
                token=token,
            )
        else:
            result = coordinator.request(
                task_id=task.pk,
                user_id=request.user.pk,
                action=action,
            )
    except AdmissionInvalid as error:
        raise ApiConflict("准入许可无效或已过期，请重新申请。", code="ADMISSION_TOKEN_INVALID") from error
    except AdmissionUnavailable as error:
        raise ApiConflict(
            "当前任务入口正在保护中，请稍后重试。",
            code="ADMISSION_UNAVAILABLE",
            status=503,
        ) from error
    if result.status == "queued":
        return Response(
            admission_result_payload(result, poll_url=_admission_poll_url(task.pk)),
            status=202,
            headers={"Retry-After": str(result.retry_after or 3)},
        )
    return result.token


class StudentApiMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.STUDENT,)


class StudentAdmissionStatusView(StudentApiMixin, APIView):
    @extend_schema(request=StudentAdmissionStatusSerializer, responses=StudentAdmissionStatusResponseSerializer)
    def post(self, request, task_id):
        task = _student_task(request.user, task_id)
        document = StudentAdmissionStatusSerializer(data=request.data)
        document.is_valid(raise_exception=True)
        action = request.data.get("action")
        if action not in {"start", "resume"}:
            raise ApiConflict("准入动作无效。", code="ADMISSION_ACTION_INVALID")
        try:
            result = AdmissionCoordinator().status(
                task_id=task.pk,
                user_id=request.user.pk,
                action=action,
                token=document.validated_data["admission_token"],
            )
        except AdmissionInvalid as error:
            raise ApiConflict("准入许可无效或已过期，请重新申请。", code="ADMISSION_TOKEN_INVALID") from error
        except AdmissionUnavailable as error:
            raise ApiConflict(
                "当前任务入口正在保护中，请稍后重试。",
                code="ADMISSION_UNAVAILABLE",
                status=503,
            ) from error
        return Response(
            admission_result_payload(result, poll_url=_admission_poll_url(task.pk)),
            status=202 if result.status == "queued" else 200,
            headers={"Retry-After": str(result.retry_after or 3)} if result.status == "queued" else None,
        )


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
        admission_token = None
        if existing is None or not AssessmentEvent.objects.filter(
            session=existing, event_type="item_entered"
        ).exists():
            admission_token = _admission_gate(request, task, "start")
            if isinstance(admission_token, Response):
                return admission_token
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
            _entry_projection(task, session, admission_token=admission_token, admission_action="start"),
            status=200 if existing is not None else 201,
        )


class StudentTaskResumeView(StudentApiMixin, APIView):
    @extend_schema(request=None, responses=StudentSessionEntrySerializer)
    def post(self, request, task_id):
        task = _student_task(request.user, task_id)
        existing = ExperimentSession.objects.filter(
            class_task=task,
            participant_snapshot__student=request.user,
        ).first()
        admission_token = None
        if existing is None or not AssessmentEvent.objects.filter(
            session=existing, event_type="item_entered"
        ).exists():
            admission_token = _admission_gate(request, task, "resume")
            if isinstance(admission_token, Response):
                return admission_token
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
        return Response(_entry_projection(task, session, admission_token=admission_token, admission_action="resume"))


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
    def session(self, request, session_id, *, locked=False):
        if locked:
            get_object_or_404(
                ExperimentSession.objects.only('pk').select_for_update(of=('self',)),
                pk=session_id, student=request.user,
            )
        sessions = ExperimentSession.objects.select_related(
            'version__experiment__course', 'class_task', 'schedule_revision', 'capture_contract',
        )
        return get_object_or_404(sessions, pk=session_id, student=request.user)

    def assert_content_access(self, request, session, at):
        if session.class_task_id:
            _student_task(request.user, session.class_task_id)
        try:
            _validate_course_access(session, request_received_at=at)
        except ValidationError as error:
            raise ApiConflict('; '.join(error.messages), code='SESSION_NOT_ENTERABLE') from error

    def entry_decision(self, session, attempt, at):
        if attempt.status in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED):
            if not (session.class_task_id and attempt.status != Attempt.Status.SUBMITTED
                    and can_recover_session(session.class_task, session, at=at)):
                return DeadlineDecision(False, 'ATTEMPT_CLOSED')
        if session.status != ExperimentSession.Status.IN_PROGRESS:
            decision = DeadlineDecision(False, 'SESSION_CLOSED')
        elif session.class_task_id:
            decision = DeadlineDecisionService.can_write_response(session.class_task, session, attempt, at)
        else:
            deadline = min((value for value in (session.deadline_at,
                attempt.current_effective_deadline_at or attempt.initial_effective_deadline_at) if value is not None), default=None)
            decision = DeadlineDecision(False, 'DEADLINE_EXPIRED', deadline) if deadline and at >= deadline else DeadlineDecision(True)
        if not decision.allowed and session.class_task_id and can_recover_session(session.class_task, session, at=at):
            return DeadlineDecision(True)
        return decision

    @extend_schema(responses=StudentRuntimeQuestionEntrySerializer)
    def get(self, request, session_id, assignment_id):
        request_received_at = timezone.now()
        session = self.session(request, session_id)
        self.assert_content_access(request, session, request_received_at)
        assignment = get_object_or_404(
            StudentQuestionAssignment.objects.select_related('snapshot', 'item_instance'),
            pk=assignment_id, session=session,
        )
        attempt = get_object_or_404(Attempt.objects.select_related('current_response_snapshot'), assignment=assignment)
        attempt.session = session
        attempt.assignment = assignment
        return self.runtime_response(attempt, None, request_received_at)

    @extend_schema(request=StudentRuntimeQuestionEnterSerializer, responses=StudentRuntimeQuestionEntrySerializer)
    def post(self, request, session_id, assignment_id):
        request_received_at = timezone.now()
        document = StudentRuntimeQuestionEnterSerializer(data=request.data)
        document.is_valid(raise_exception=True)
        material = document.validated_data
        admission_token = material.get('admission_token')
        admission_action = material.get('admission_action')
        admission = None
        with transaction.atomic():
            session = self.session(request, session_id, locked=True)
            self.assert_content_access(request, session, timezone.now())
            admission_enabled = bool(session.class_task_id and AdmissionCoordinator.enabled_for(session.class_task_id))
            first_entry = (
                admission_enabled
                and not AssessmentEvent.objects.filter(
                    session=session, event_type='item_entered'
                ).exists()
            )
            if first_entry and (not admission_token or not admission_action):
                raise ApiConflict('首次进入题目需要有效准入许可。', code='ADMISSION_TOKEN_REQUIRED')
            assignment = get_object_or_404(
                StudentQuestionAssignment.objects.select_related('snapshot', 'item_instance'),
                pk=assignment_id, session=session,
            )
            attempt = get_object_or_404(
                Attempt.objects.select_for_update(of=('self',)).select_related('current_response_snapshot'),
                assignment=assignment,
            )
            attempt.session = session
            attempt.assignment = assignment
            event = AssessmentEvent.objects.filter(session=session, event_type='item_entered',
                correlation_id=material['navigation_id']).first()
            if event is not None:
                if (event.assignment_id != assignment.pk
                        or event.payload['entry_reason'] != material['reason']
                        or event.client_wall_time_utc != material['client_occurred_at']
                        or event.client_monotonic_ms != material['client_monotonic_ms']
                        or event.client_time_origin != material['client_time_origin']):
                    raise ApiConflict('进入标识已绑定不同的题目或时钟材料。', code='ENTRY_MATERIAL_CONFLICT')
                visit = get_object_or_404(QuestionVisitEvent, pk=event.payload['visit_id'], session=session, attempt=attempt)
                visit_entry = RecordedVisitEntry(attempt=attempt, visit=visit, entry_event=event,
                    navigation_id=event.correlation_id)
                decision = self.entry_decision(session, attempt, request_received_at)
            else:
                item_deadline = attempt.current_effective_deadline_at or attempt.initial_effective_deadline_at
                if (item_deadline and request_received_at >= item_deadline
                        and attempt.status not in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED)
                        and not self.entry_decision(session, attempt, request_received_at).allowed):
                    raise ApiConflict('本题作答时间已结束。', code='SESSION_NOT_ENTERABLE')
                if first_entry:
                    admission = AdmissionCoordinator()
                    try:
                        grant = admission.status(task_id=session.class_task_id, user_id=request.user.pk,
                                                 action=admission_action, token=admission_token)
                        if grant.status != 'admitted':
                            raise AdmissionInvalid
                    except AdmissionInvalid as error:
                        raise ApiConflict('准入许可无效或已过期，请返回任务列表重试。', code='ADMISSION_TOKEN_INVALID') from error
                    except AdmissionUnavailable as error:
                        raise ApiConflict('当前任务入口正在保护中，请稍后重试。', code='ADMISSION_UNAVAILABLE', status=503) from error
                try:
                    visit_entry = record_visit_entry(session.pk, assignment.pk, material['reason'],
                        material['client_occurred_at'], request_received_at=request_received_at,
                        navigation_id=material['navigation_id'], client_monotonic_ms=material['client_monotonic_ms'],
                        client_time_origin=material['client_time_origin'])
                except ValidationError as error:
                    raise ApiConflict('; '.join(error.messages), code='SESSION_NOT_ENTERABLE') from error
                decision = DeadlineDecision(False, 'ATTEMPT_CLOSED') if visit_entry.attempt.status in (
                    Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED,
                ) else DeadlineDecision(True)
                if admission is not None:
                    def consume_admission():
                        try:
                            admission.consume(task_id=session.class_task_id, user_id=request.user.pk,
                                              action=admission_action, token=admission_token)
                        except AdmissionInvalid as error:
                            raise ApiConflict('准入许可已失效，请核验后重试。', code='ADMISSION_TOKEN_INVALID') from error
                        except AdmissionUnavailable as error:
                            raise ApiConflict('当前任务入口正在保护中，请稍后重试。', code='ADMISSION_UNAVAILABLE', status=503) from error
                    transaction.on_commit(consume_admission)
            runtime_attempt = visit_entry.attempt
            runtime_entry = visit_entry

        return self.runtime_response(runtime_attempt, runtime_entry, request_received_at, decision=decision)

    def runtime_response(self, attempt, visit_entry, request_received_at, *, decision=None):
        assignment = attempt.assignment
        session = attempt.session
        if decision is None:
            decision = self.entry_decision(session, attempt, request_received_at)
        assignments = list(
            session.assignments.select_related("attempt").order_by(
                "assigned_sequence_index"
            )
        )
        from ..research_tool_services import available_tools, due_rules, tool_configuration
        tool_events = list(AssessmentEvent.objects.filter(attempt=attempt, event_type='research_tool')
                           .order_by('server_ingest_sequence_no'))
        research_tools = available_tools(attempt, events=tool_events)
        research_tools['rules'] = due_rules(attempt, events=tool_events)
        payload = {
            'offline_authorization': {'user_id': session.student_id, 'session_id': session.pk, 'assignment_id': assignment.pk},
            'entry_allowed': decision.allowed,
            'entry_denial_code': decision.code,
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
                "save_draft_url": reverse("assessment_api:save_draft", args=[attempt.pk]),
                "submit_url": reverse("assessment_api:submit", args=[attempt.pk]),
                "skip_url": reverse("assessment_api:skip", args=[attempt.pk]),
                "finish_url": reverse("assessment_api:finish", args=[session.pk]),
            },
            "process": {
                "visit_id": visit_entry.visit.pk if visit_entry else None,
                "visit_index": visit_entry.visit.visit_index if visit_entry else None,
                "entry_event_id": str(visit_entry.entry_event.pk) if visit_entry else None,
                "navigation_id": str(visit_entry.navigation_id) if visit_entry else None,
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
        if visit_entry is not None:
            payload['entry_receipt'] = {
                'navigation_id': visit_entry.navigation_id, 'session_id': session.pk,
                'assignment_id': assignment.pk, 'attempt_id': attempt.pk,
                'visit_id': visit_entry.visit.pk, 'visit_index': visit_entry.visit.visit_index,
                'entry_event_id': visit_entry.entry_event.pk, 'entered_at': visit_entry.entry_event.server_received_at,
            }
        if not session.capture_contract_id or session.capture_contract.contract_name != 'capture-v2':
            payload.pop('research_tools', None)
            payload['actions'].pop('research_tools_url', None)
            payload['actions'].pop('resolve_branch_url', None)
            for key in ('capture_version', 'active_response_event_id', 'response_version', 'writer_lease_acquire_url', 'writer_lease_renew_url', 'writer_lease_release_url'):
                payload['process'].pop(key, None)
        else:
            from ..capture_bundle_services import capture_bundle_capability
            payload['process']['capture_bundle'] = capture_bundle_capability(session)
            from ..atomic_submission_services import atomic_submission_capability
            atomic_capability = atomic_submission_capability(attempt)
            if atomic_capability['enabled']:
                payload['process']['atomic_submission'] = atomic_capability
            from ..saved_navigation_services import saved_navigation_capability
            navigation_capability = saved_navigation_capability(attempt)
            if navigation_capability['enabled']:
                payload['process']['save_and_leave'] = navigation_capability
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


_STATUS_PARAMETER = OpenApiParameter('status_only', bool, description='Return only the authorized result version and comparison state; no result computation.')


def _result_response_schema(name, serializer):
    return PolymorphicProxySerializer(component_name=name, serializers=[serializer, StudentProjectionStatusSerializer], resource_type_field_name=None)


def _student_status(enrollment, **kwargs):
    from apps.analytics.student_result_status import student_result_status
    try:
        return student_result_status(enrollment, **kwargs)
    except LookupError as error:
        raise Http404 from error


def _status_response(request, metadata):
    payload = StudentProjectionStatusSerializer(instance=metadata).data
    etag = f'W/"student-status-{metadata["version"]}"'
    headers = {'ETag': etag, 'Cache-Control': 'private, no-cache'}
    tags = parse_etags(request.headers.get('If-None-Match', ''))
    if '*' in tags or etag.removeprefix('W/') in [tag.removeprefix('W/') for tag in tags]:
        return Response(status=304, headers=headers)
    return Response(payload, headers=headers)


def _versioned_result(payload, metadata):
    # Metadata precedes the full read. A worker state transition between those
    # reads must not label a pending payload with a reusable ready version.
    payload.pop('version', None)
    if payload.get('comparison_status') == metadata.get('comparison_status'):
        payload['version'] = metadata['version']
    return payload


class StudentTaskResultView(StudentApiMixin, APIView):
    @extend_schema(parameters=[_STATUS_PARAMETER], responses=_result_response_schema('StudentTaskResultResponse', StudentResultResponseSerializer))
    def get(self, request, task_id):
        course_id = get_object_or_404(ClassExperimentTask.objects.values('experiment__course_id'), pk=task_id)['experiment__course_id']
        enrollment = _student_enrollment(request.user, course_id)
        metadata = _student_status(enrollment, task_id=task_id, require_results=False)
        if request.query_params.get('status_only') in ('1', 'true'):
            return _status_response(request, metadata)
        task = _student_task(request.user, task_id)
        return Response(
            _versioned_result(serialize_student_result(build_student_task_result(task, request.user)), metadata)
        )


def _learning_enrollment(student, course_id):
    if not settings.STUDENT_LEARNING_ANALYTICS_ENABLED:
        raise Http404
    return _student_enrollment(student, course_id)


class StudentLearningView(StudentApiMixin, APIView):
    @extend_schema(parameters=[_STATUS_PARAMETER], responses=_result_response_schema('StudentLearningResponse', StudentLearningProjectionSerializer))
    def get(self, request, course_id):
        from apps.analytics.teaching.student_projection import (
            build_student_learning_projection,
            student_projection_to_api,
        )

        enrollment = _learning_enrollment(request.user, course_id)
        metadata = _student_status(enrollment)
        if request.query_params.get('status_only') in ('1', 'true'):
            return _status_response(request, metadata)
        try:
            projection = build_student_learning_projection(enrollment)
        except LookupError as error:
            raise Http404 from error
        payload = student_projection_to_api(projection)
        _versioned_result(payload, metadata)
        return Response(StudentLearningProjectionSerializer(instance=payload).data)


class StudentLearningExperimentView(StudentApiMixin, APIView):
    @extend_schema(parameters=[_STATUS_PARAMETER], responses=_result_response_schema('StudentLearningExperimentResponse', StudentLearningExperimentSerializer))
    def get(self, request, course_id, experiment_id):
        enrollment = _learning_enrollment(request.user, course_id)
        metadata = _student_status(enrollment, experiment_id=experiment_id)
        if request.query_params.get('status_only') in ('1', 'true'):
            return _status_response(request, metadata)
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
        _versioned_result(payload, metadata)
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
    @extend_schema(parameters=[_STATUS_PARAMETER], responses=_result_response_schema('StudentLearningQuestionResponse', StudentLearningQuestionSerializer))
    def get(self, request, course_id, experiment_id, question_id):
        from apps.analytics.teaching.student_projection import (
            build_student_question_projection,
            student_projection_to_api,
        )

        enrollment = _learning_enrollment(request.user, course_id)
        metadata = _student_status(enrollment, experiment_id=experiment_id, question_id=question_id)
        if request.query_params.get('status_only') in ('1', 'true'):
            return _status_response(request, metadata)
        try:
            projection = build_student_question_projection(
                enrollment,
                experiment_id=experiment_id,
                question_id=question_id,
            )
        except LookupError as error:
            raise Http404 from error
        payload = student_projection_to_api(projection)
        _versioned_result(payload, metadata)
        return Response(StudentLearningQuestionSerializer(instance=payload).data)
