import hashlib
import json
from config.capture_timing import request_phase

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, F, Prefetch, Q
from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils import timezone
from django.utils.http import parse_etags
from drf_spectacular.utils import extend_schema, OpenApiParameter, OpenApiResponse, PolymorphicProxySerializer
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
    TaskManualClosure,
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
from ..services import can_recover_session, resume_task_session
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
    StudentPreparedStartSerializer,
    StudentRuntimeQuestionEntrySerializer,
    StudentRuntimeQuestionEnterSerializer,
    StudentAdmissionStatusSerializer,
    StudentAdmissionStatusResponseSerializer,
    StudentPreparedStartPendingSerializer,
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


def _student_task_queryset(student, *, course_id=None, include_projection=True):
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
                "manual_closures",
                queryset=TaskManualClosure.objects.only('pk', 'class_task_id', 'closed_at', 'schedule_revision_no')
                    .order_by('-closed_at', '-pk'),
                to_attr="current_manual_closures",
            ),
            Prefetch(
                "preview_class_result__participants",
                queryset=PublishPreviewParticipant.objects.filter(student=student).only('pk', 'class_result_id', 'item_count'),
                to_attr="current_student_preview_participants",
            ),
            Prefetch(
                "participant_snapshots",
                queryset=ExperimentVersionParticipantSnapshot.objects.filter(
                    student=student
                ).only('pk', 'class_task_id'),
                to_attr="current_student_participants",
            ),
            Prefetch(
                "sessions",
                queryset=ExperimentSession.objects.filter(student=student).exclude(status=ExperimentSession.Status.PREPARED).annotate(
                    task_attempt_count=Count('attempts'),
                    task_completed_count=Count('attempts', filter=Q(attempts__status__in=(
                        Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED))),
                ),
                to_attr="current_student_sessions",
            ),
        )
        .order_by("-published_at", "-pk")
        .distinct()
    )
    if course_id is not None:
        queryset = queryset.filter(experiment__course_id=course_id)
    if include_projection:
        queryset = queryset.annotate(version_item_count=Count('release_snapshot__question_snapshots', distinct=True))
    return queryset


def _task_projection(task, *, course):
    schedule = next(iter(task.latest_schedule_revisions), None)
    session = next(iter(task.current_student_sessions), None)
    if session is not None:
        session.version = task.release_snapshot
        total = session.task_attempt_count
        completed = session.task_completed_count
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
            else task.version_item_count
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
    tasks = _student_task_queryset(student, include_projection=False).prefetch_related(None).select_related(None)
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


class StudentApiMixin:
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.STUDENT,)


class StudentAdmissionStatusView(StudentApiMixin, APIView):
    @extend_schema(request=StudentAdmissionStatusSerializer, responses=StudentAdmissionStatusResponseSerializer)
    def post(self, request, task_id):
        document = StudentAdmissionStatusSerializer(data=request.data)
        document.is_valid(raise_exception=True)
        action = request.data.get("action")
        if action not in {"start", "resume"}:
            raise ApiConflict("准入动作无效。", code="ADMISSION_ACTION_INVALID")
        try:
            result = AdmissionCoordinator().status(
                task_id=task_id,
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
            admission_result_payload(result, poll_url=_admission_poll_url(task_id)),
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
    @extend_schema(request=StudentPreparedStartSerializer, responses={200: StudentSessionEntrySerializer, 201: StudentSessionEntrySerializer, 202: StudentPreparedStartPendingSerializer, 429: OpenApiResponse(description='START_BUSY; retry the same start intent after Retry-After.')})
    def post(self, request, task_id):
        from .prepared_start import prepared_start_request
        return prepared_start_request(request, task_id)


class StudentTaskResumeView(StudentApiMixin, APIView):
    @extend_schema(request=None, responses=StudentSessionEntrySerializer)
    def post(self, request, task_id):
        task = _student_task(request.user, task_id)
        existing = ExperimentSession.objects.filter(
            class_task=task,
            participant_snapshot__student=request.user,
        ).first()
        if existing is not None and existing.status == ExperimentSession.Status.PREPARED:
            raise ApiConflict("该任务尚未开始。", code="SESSION_NOT_STARTED")
        if existing is not None and existing.assessment_flow_version != 1:
            existing = _canonical_paper_session(existing)
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


def _canonical_paper_session(session):
    from ..prepared_services import ensure_canonical_paper_session
    try:
        return ensure_canonical_paper_session(session)
    except ValidationError as error:
        raise ApiConflict('该历史作答需由教师处理后继续使用整卷流程。',
                          code='WHOLE_PAPER_UPGRADE_REQUIRED', status=409) from error


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
        closed_attempt = attempt.status in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED)
        if session.status != ExperimentSession.Status.IN_PROGRESS:
            decision = DeadlineDecision(False, 'SESSION_CLOSED')
        elif session.class_task_id:
            # Reviewing a locked answer is an entry, not permission to edit it.
            decision = (DeadlineDecisionService.can_enter_item(session.class_task, session, at)
                        if closed_attempt or session.assessment_flow_version == 1 else
                        DeadlineDecisionService.can_write_response(session.class_task, session, attempt, at))
        else:
            deadline = min((value for value in (session.deadline_at,
                None if closed_attempt else attempt.current_effective_deadline_at or attempt.initial_effective_deadline_at) if value is not None), default=None)
            decision = DeadlineDecision(False, 'DEADLINE_EXPIRED', deadline) if deadline and at >= deadline else DeadlineDecision(True, deadline=deadline)
        if not decision.allowed and session.class_task_id and can_recover_session(session.class_task, session, at=at):
            return DeadlineDecision(True)
        return decision

    @extend_schema(responses=StudentRuntimeQuestionEntrySerializer)
    def get(self, request, session_id, assignment_id):
        request_received_at = timezone.now()
        session = self.session(request, session_id)
        self.assert_content_access(request, session, request_received_at)
        session = _canonical_paper_session(session)
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
        return self.enter(request, session_id, assignment_id, request.data)

    def enter(self, request, session_id, assignment_id, document):
        request_received_at = timezone.now()
        document = StudentRuntimeQuestionEnterSerializer(data=document)
        document.is_valid(raise_exception=True)
        material = document.validated_data
        with transaction.atomic():
            session = self.session(request, session_id, locked=True)
            self.assert_content_access(request, session, timezone.now())
            session = _canonical_paper_session(session)
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
                        or event.payload.get('client_instance_id') != (
                            str(material['client_instance_id']) if material.get('client_instance_id') else None)
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
                try:
                    with request_phase(request, 'question_entry'):
                        visit_entry = record_visit_entry(session.pk, assignment.pk, material['reason'],
                            material['client_occurred_at'], request_received_at=request_received_at,
                            navigation_id=material['navigation_id'], client_monotonic_ms=material['client_monotonic_ms'],
                            client_time_origin=material['client_time_origin'],
                            entry_client_instance_id=material.get('client_instance_id'))
                except ValidationError as error:
                    raise ApiConflict('; '.join(error.messages), code='SESSION_NOT_ENTERABLE') from error
                decision = self.entry_decision(visit_entry.attempt.session, visit_entry.attempt, request_received_at)
            runtime_attempt = visit_entry.attempt
            runtime_entry = visit_entry
            initial_lease = self.entry_writer_lease(runtime_attempt, material.get('client_instance_id'),
                replay=event is not None)

        with request_phase(request, 'question_projection'):
            response = self.runtime_response(runtime_attempt, runtime_entry, request_received_at, decision=decision)
            if initial_lease is not None:
                response.data['initial_writer_lease'] = initial_lease
            return response

    def entry_writer_lease(self, attempt, client_id, *, replay):
        from ..capture_v2_services import uses_capture_v2
        from ..writer_lease_services import _bind_locked_attempt, acquire_attempt_writer_lease, WriterLeaseConflict
        now = timezone.now()
        session = attempt.session
        if (client_id is None or not uses_capture_v2(session) or session.status != ExperimentSession.Status.IN_PROGRESS
                or attempt.status in (Attempt.Status.SUBMITTED, Attempt.Status.TIMED_OUT, Attempt.Status.UNANSWERED)):
            return None
        decision = (DeadlineDecisionService.can_write_response(session.class_task, session, attempt, now)
                    if session.class_task_id else self.entry_decision(session, attempt, now))
        if not decision.allowed:
            return None
        if replay:
            # Historical entry receipts confirm a visit, never authority to recreate a lease.
            if (attempt.writer_client_instance_id != client_id or attempt.writer_lease_token is None
                    or not attempt.writer_lease_expires_at or attempt.writer_lease_expires_at <= now):
                return None
            return {'writer_lease_token': str(attempt.writer_lease_token),
                    'writer_client_instance_id': str(client_id),
                    'expires_at': attempt.writer_lease_expires_at.isoformat()}
        try:
            lease = acquire_attempt_writer_lease(_bind_locked_attempt(attempt),
                client_instance_id=client_id, now=now, _locked_attempt=attempt)
        except WriterLeaseConflict as error:
            raise ApiConflict(str(error), code='writer_lease_conflict') from error
        return {'writer_lease_token': str(lease.token), 'writer_client_instance_id': str(client_id),
                'expires_at': lease.expires_at.isoformat()}

    def runtime_response(self, attempt, visit_entry, request_received_at, *, decision=None):
        if attempt.session.assessment_flow_version != 1:
            raise ApiConflict('该历史作答需由教师处理后继续使用整卷流程。',
                              code='WHOLE_PAPER_UPGRADE_REQUIRED', status=409)
        if attempt.session.status == ExperimentSession.Status.PREPARED:
            raise ApiConflict('请先完成服务端激活。', code='SESSION_NOT_STARTED', status=409)
        from ..prepared_services import prepared_runtime
        return Response(prepared_runtime(attempt, visit_entry,
            request_received_at=request_received_at, decision=decision),
            headers={'Cache-Control': 'no-store'})


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
