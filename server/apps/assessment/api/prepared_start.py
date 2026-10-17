"""One-request prepared activation with selectable admission coordination."""
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import DatabaseError
from django.utils import timezone
from rest_framework.response import Response

from ..admission import AdmissionCoordinator, AdmissionInvalid, AdmissionUnavailable
from ..models import ExperimentSession
from ..prepared_models import PreparedSessionArtifact
from ..prepared_admission import ActivationBusy, ActivationExecuting
from ..prepared_services import (PreparationNotReady, activate_prepared_session,
                                 enqueue_preparation_repair, ensure_canonical_paper_session)
from apps.experiments.models import ExperimentVersionParticipantSnapshot
from .serializers import StudentPreparedStartSerializer


def prepared_start_request(request, task_id):
    from .views import ApiConflict, _entry_projection, _student_task
    document = StudentPreparedStartSerializer(data=request.data)
    document.is_valid(raise_exception=True)
    values = document.validated_data
    _student_task(request.user, task_id)
    participant = ExperimentVersionParticipantSnapshot.objects.filter(
        class_task_id=task_id, student=request.user).only('pk', 'version_id', 'class_task_id').first()
    if participant is None:
        raise ApiConflict('你不在该任务的参与者名单中。', code='PARTICIPANT_NOT_AUTHORIZED', status=403)
    # Historical capture remains immutable; compatible unfinished work receives
    # paper projections without rerolling questions or restarting its timer.
    historical = ExperimentSession.objects.filter(class_task_id=task_id, student=request.user,
        assessment_flow_version=0).select_related('class_task', 'current_assignment').first()
    if historical:
        try:
            historical = ensure_canonical_paper_session(historical)
        except ValidationError as error:
            raise ApiConflict('该历史作答需由教师处理后继续使用整卷流程。',
                              code='WHOLE_PAPER_UPGRADE_REQUIRED', status=409) from error
        return Response(_entry_projection(historical.class_task, historical), headers={'Cache-Control': 'no-store'})
    artifact = PreparedSessionArtifact.objects.filter(session__class_task_id=task_id,
        session__student=request.user, session__assessment_flow_version=1).only(
            'pk', 'ready', 'session_id', 'activation_receipt').first()
    if artifact is None or not artifact.ready:
        enqueue_preparation_repair(participant)
        raise ApiConflict('个人试卷尚未就绪，后台正在修复，请稍后重试。',
                          code='PREPARATION_NOT_READY', status=409)
    if not artifact.activation_receipt:
        upgraded = ExperimentSession.objects.filter(pk=artifact.session_id,
            status=ExperimentSession.Status.IN_PROGRESS).select_related('class_task', 'current_assignment').first()
        if upgraded is not None:
            return Response(_entry_projection(upgraded.class_task, upgraded),
                            headers={'Cache-Control': 'no-store'})
    if not (settings.ASSESSMENT_ADMISSION_ENABLED and settings.ASSESSMENT_ADMISSION_GLOBAL_ENABLED):
        raise ApiConflict('启动保护尚未配置，暂不能开始新流程。', code='ADMISSION_UNAVAILABLE', status=503)
    client_id, navigation_id = values.get('client_instance_id'), values.get('navigation_id')
    if client_id is None or navigation_id is None:
        raise ApiConflict('缺少可恢复的启动客户端标识。', code='START_IDENTITY_REQUIRED')
    try:
        if settings.ASSESSMENT_PREPARED_ADMISSION_MODE == 'postgres_transaction':
            execution_id = uuid.uuid4()
            _, receipt = activate_prepared_session(student=request.user, task_id=task_id,
                client_instance_id=client_id, navigation_id=navigation_id,
                request_received_at=timezone.now(), execution_id=execution_id,
                client_wall_time=values.get('client_occurred_at'),
                client_monotonic_ms=values.get('client_monotonic_ms'),
                client_time_origin=values.get('client_time_origin'))
            return Response(receipt, status=201 if receipt['admission_execution_id'] == str(execution_id) else 200,
                            headers={'Cache-Control': 'no-store'})
        coordinator = AdmissionCoordinator()
        if artifact.activation_receipt:
            _, receipt = activate_prepared_session(student=request.user, task_id=task_id,
                client_instance_id=client_id, navigation_id=navigation_id)
            return Response(receipt, headers={'Cache-Control': 'no-store'})
        claim = coordinator.request(task_id=task_id, user_id=request.user.pk, action='start')
        if claim.status == 'busy':
            return Response({'error': {'code': 'START_BUSY', 'message': '启动入口繁忙，正在重试。', 'fields': {}}},
                status=429, headers={'Cache-Control': 'no-store', 'Retry-After': '1'})
        if claim.status == 'succeeded':
            _, receipt = activate_prepared_session(student=request.user, task_id=task_id,
                client_instance_id=client_id, navigation_id=navigation_id)
            return Response(receipt, headers={'Cache-Control': 'no-store'})
        if claim.status != 'executing':
            raise AdmissionInvalid()
        if not claim.execution_claimed:
            return Response({'status': 'executing', 'retry_after_seconds': 1}, status=202,
                headers={'Cache-Control': 'no-store', 'Retry-After': '1'})
        try:
            _, receipt = activate_prepared_session(
                student=request.user, task_id=task_id, client_instance_id=client_id,
                navigation_id=navigation_id, request_received_at=timezone.now(),
                admission_token=claim.token, execution_id=claim.execution_id,
                client_wall_time=values.get('client_occurred_at'),
                client_monotonic_ms=values.get('client_monotonic_ms'),
                client_time_origin=values.get('client_time_origin'))
        except Exception:
            # Only a proven rollback is released here; ambiguous DB failures remain occupied.
            import sys
            if not isinstance(sys.exception(), DatabaseError):
                coordinator.release(task_id=task_id, user_id=request.user.pk, action='start',
                    token=claim.token, execution_id=claim.execution_id, success=False)
            raise
        try:
            coordinator.release(task_id=task_id, user_id=request.user.pk, action='start',
                token=claim.token, execution_id=claim.execution_id, success=True)
        except AdmissionUnavailable:
            # The durable receipt is authoritative. The reconciler will release the slot.
            pass
        return Response(receipt, status=201, headers={'Cache-Control': 'no-store'})
    except AdmissionInvalid as error:
        raise ApiConflict('启动许可已失效，请恢复原操作后重新申请。', code='ADMISSION_TOKEN_INVALID') from error
    except AdmissionUnavailable as error:
        raise ApiConflict('启动入口正在保护中，请稍后重试。', code='ADMISSION_UNAVAILABLE', status=503) from error
    except ActivationBusy:
        return Response({'error': {'code': 'START_BUSY', 'message': '启动入口繁忙，正在重试。', 'fields': {}}},
            status=429, headers={'Cache-Control': 'no-store', 'Retry-After': '1'})
    except ActivationExecuting:
        return Response({'status': 'executing', 'retry_after_seconds': 1}, status=202,
            headers={'Cache-Control': 'no-store', 'Retry-After': '1'})
    except DatabaseError as error:
        if settings.ASSESSMENT_PREPARED_ADMISSION_MODE != 'postgres_transaction':
            raise
        raise ApiConflict('启动数据库暂不可用，请稍后重试。', code='ADMISSION_UNAVAILABLE', status=503) from error
    except (PreparationNotReady, ValidationError) as error:
        code = error.messages[0] if getattr(error, 'messages', None) else 'START_NOT_ALLOWED'
        raise ApiConflict(str(error), code=code, status=409) from error
