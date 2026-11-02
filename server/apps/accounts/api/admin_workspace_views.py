from django.db import IntegrityError, OperationalError, transaction
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db.models import Count, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.exceptions import APIException, NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView
from drf_spectacular.utils import extend_schema

from apps.api.permissions import RolePermission
from apps.audit.models import AuditLog
from apps.audit.services import log_action
from apps.accounts.models import User, Course, ClassGroup, DataPurgeRequest
from apps.accounts import admin_workspace as service


class AdminConflict(APIException):
    status_code = 409
    default_code = "CONFLICT"


class AdminView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = ()

    def handle_exception(self, exc):
        from apps.experiments.research_deletion import DeletionConflict
        if isinstance(exc, DeletionConflict):
            exc = AdminConflict("；".join(exc.messages))
        elif isinstance(exc, OperationalError) and getattr(exc.__cause__, "sqlstate", None) == "55P03":
            exc = AdminConflict("相关数据正在处理，请稍后重新预览。")
        elif isinstance(exc, DjangoValidationError):
            exc = ValidationError(getattr(exc, 'message_dict', {'detail': exc.messages}))
        elif isinstance(exc, IntegrityError):
            exc = ValidationError('该操作与现有记录冲突，请刷新后重新确认。')
        return super().handle_exception(exc)


def task_rows(*, limit=None):
    from apps.assessment.models import ExperimentSession
    from apps.experiments.models import ClassExperimentTask
    from apps.experiments.api.views import _task_projection, _task_projection_queryset
    from apps.experiments.publication_services import database_now
    queryset = _task_projection_queryset(ClassExperimentTask.objects.filter(experiment__course__deleted_at__isnull=True).select_related('experiment__course')).order_by('-pk')
    tasks = list(queryset[:limit] if limit is not None else queryset)
    session_counts = {row['class_task_id']: row for row in ExperimentSession.objects.filter(
        class_task_id__in=[task.pk for task in tasks],
    ).order_by().values('class_task_id').annotate(
        submitted_count=Count('student_id', distinct=True, filter=Q(status='completed')),
        started_count=Count('student_id', distinct=True, filter=~Q(status='prepared')),
    )}
    now = database_now()
    rows = []
    for task in tasks:
        row = _task_projection(task, now=now)
        row['status'] = row['business_status']
        row['status_label'] = {'open': '进行中', 'scheduled': '未开始', 'closed': '已结束',
            'cancelled': '已取消', 'invalidated': '已作废', 'manual_closed': '已提前结束'}.get(row['status'], row['status'])
        counts = session_counts.get(task.pk, {})
        row['submitted_count'] = counts.get('submitted_count', 0)
        row['started_count'] = counts.get('started_count', 0)
        row.update({'id': task.pk, 'course_id': task.experiment.course_id,
                    'course_name': task.experiment.course.name, 'experiment_id': task.experiment_id,
                    'experiment_name': task.experiment.name, 'class_name': task.class_group.name,
                    'detail_url': f'/manage/data/?tab=tasks&task_id={task.pk}',
                    'api_url': f'/api/v1/teacher/class-tasks/{task.pk}/',
                    'schedule_url': f'/api/v1/teacher/class-tasks/{task.pk}/schedule-revisions/',
                    'close_url': f'/api/v1/teacher/class-tasks/{task.pk}/manual-close/'})
        rows.append(row)
    return rows


def experiment_rows():
    from apps.experiments.models import Experiment
    return [{'id': e.pk, 'name': e.name, 'course_id': e.course_id,
             'course_name': e.course.name if e.course else '独立实验',
             'status': e.status, 'status_label': e.get_status_display(),
             'task_count': e.class_tasks.count(),
             'detail_url': f'/manage/courses/{e.course_id}/experiments/{e.pk}/' if e.course_id else f'/manage/experiments/{e.pk}/'}
            for e in Experiment.objects.filter(course__deleted_at__isnull=True).select_related('course').order_by('-pk')]


@extend_schema(responses=dict)
class AdminAccessView(AdminView):
    def get(self, request):
        return Response({'allowed': True})


@extend_schema(responses=dict)
class AdminCatalogView(AdminView):
    def get(self, request, section):
        from apps.question_bank.models import QuestionBank, Question, Concept
        from apps.experiments.models import Experiment
        from apps.analytics.models import ExportJob
        if section == 'users':
            return Response({'items': [service.user_row(u) for u in User.objects.filter(deleted_at__isnull=True).order_by('role', 'username')]})
        if section == 'courses':
            return Response({'items': [service.course_row(c) for c in Course.objects.select_related('owner')]})
        if section == 'classes':
            return Response({'items': [service.class_row(c) for c in ClassGroup.objects.filter(course__deleted_at__isnull=True).select_related('course', 'owner')]})
        if section == 'audit':
            return Response({'items': [service.audit_row(a) for a in AuditLog.objects.select_related('actor').all()]})
        if section == 'overview':
            return Response({'metrics': {'users': User.objects.filter(deleted_at__isnull=True).count(), 'teachers': User.objects.filter(role='teacher', deleted_at__isnull=True).count(),
                'students': User.objects.filter(role='student', deleted_at__isnull=True).count(), 'courses': Course.objects.count(),
                'classes': ClassGroup.objects.filter(course__deleted_at__isnull=True).count(), 'banks': QuestionBank.objects.filter(course__deleted_at__isnull=True).count(),
                'experiments': Experiment.objects.filter(course__deleted_at__isnull=True).count(), 'exports': ExportJob.objects.filter(course__deleted_at__isnull=True).count()},
                'tasks': task_rows(limit=4), 'tasks_scope': 'latest', 'tasks_limit': 4,
                'recent_audit': [service.audit_row(a) for a in AuditLog.objects.select_related('actor').all()[:8]]})
        if section == 'resources':
            banks = [{'id': b.pk, 'name': b.name, 'description': b.description, 'course_id': b.course_id,
                'course_name': b.course.name if b.course else '独立题库', 'owner_name': b.owner.shown_name if b.owner else '',
                'owner_id': b.owner_id, 'created_by_id': b.created_by_id,
                'visibility': b.visibility, 'question_count': b.questions.filter(group__isnull=True).count() + b.groups.count(),
                'updated_at': b.updated_at.isoformat(),
                'detail_url': f'/manage/resources/courses/{b.course_id}/banks/{b.pk}/' if b.course_id else f'/manage/resources/banks/{b.pk}/'}
                for b in QuestionBank.objects.filter(course__deleted_at__isnull=True).select_related('course', 'owner')]
            questions = [{'id': q.pk, 'title': q.title or q.text[:100], 'bank_id': q.bank_id, 'bank_name': q.bank.name,
                'course_id': q.bank.course_id, 'course_name': q.bank.course.name if q.bank.course else '独立题库',
                'question_type': q.question_type, 'status': q.status, 'code': q.code, 'group_id': q.group_id,
                'detail_url': f'/manage/resources/courses/{q.bank.course_id}/banks/{q.bank_id}/questions/{q.pk}/edit/' if q.bank.course_id else f'/manage/resources/banks/{q.bank_id}/questions/{q.pk}/edit/'}
                for q in Question.objects.filter(bank__course__deleted_at__isnull=True).select_related('bank__course').order_by('bank_id', 'sequence')]
            concepts = [{'id': c.pk, 'name': c.name, 'code': c.code, 'description': c.description,
                'course_id': c.course_id, 'course_name': c.course.name if c.course else '公共知识点',
                'status': c.status, 'question_count': c.questions.count(),
                'detail_url': f'/manage/resources/?tab=concepts&concept_id={c.pk}'}
                for c in Concept.objects.filter(course__deleted_at__isnull=True).select_related('course')]
            return Response({'banks': banks, 'questions': questions, 'concepts': concepts})
        if section == 'data':
            from apps.analytics.api.views import _export_job_payload
            exports = []
            for job in ExportJob.objects.filter(course__deleted_at__isnull=True).select_related('course', 'created_by', 'snapshot'):
                row = _export_job_payload(job)
                row['history_url'] = f'/manage/data/?tab=exports&course_id={job.course_id}&job_id={job.pk}'
                row.update({'id': str(job.pk), 'course_id': job.course_id, 'course_name': job.course.name,
                    'created_by': job.created_by.shown_name, 'created_at': job.created_at.isoformat(),
                    'expires_at': job.expires_at.isoformat() if job.expires_at else None,
                    'progress_stage': job.progress_stage, 'kind': job.kind})
                exports.append(row)
            return Response({'experiments': experiment_rows(), 'tasks': task_rows(), 'exports': exports,
                'purge_requests': list(DataPurgeRequest.objects.values('purge_request_id', 'scope_type', 'scope_definition', 'status', 'preview_counts'))})
        raise NotFound()

    @extend_schema(request=dict, responses=dict)
    def post(self, request, section):
        try:
            if section == 'users':
                return Response(service.user_row(service.create_user(request, request.data)), status=201)
            if section == 'courses':
                return Response(service.course_row(service.save_course(request, request.data)), status=201)
            if section == 'classes':
                from apps.accounts.forms import ClassGroupForm
                from apps.accounts.class_management_services import create_course_class_group
                with transaction.atomic():
                    course = get_object_or_404(Course.objects.select_for_update(), pk=request.data.get('course_id'))
                    form = ClassGroupForm(request.data, instance=ClassGroup(course=course, owner=course.owner))
                    if not form.is_valid():
                        raise ValidationError(form.errors)
                    group = create_course_class_group(request=request, course=course, form=form)
                return Response(service.class_row(group), status=201)
        except IntegrityError as exc:
            raise ValidationError('该账号或名称已存在，请刷新后重试。') from exc
        raise NotFound()


@extend_schema(request=dict, responses=dict)
class AdminUserActionView(AdminView):
    def post(self, request, user_id, action):
        if action in ('delete-preview', 'delete'):
            from apps.accounts import admin_recycle
            if action == 'delete-preview':
                return Response(admin_recycle.preview('user', user_id, request.user))
            return Response(admin_recycle.remove(request, 'user', user_id, request.data))
        try:
            return Response(service.user_row(service.mutate_user(request, user_id, action, request.data)))
        except IntegrityError as exc:
            raise ValidationError('用户名或学号已存在。') from exc


@extend_schema(request=dict, responses=dict)
class AdminBankUpdateView(AdminView):
    def post(self, request, bank_id):
        from apps.question_bank.models import QuestionBank
        from apps.question_bank.api.management_views import (
            TeacherQuestionBankEditorView, _metadata_question_bank_form, _query_data,
            lock_question_bank, _require_standalone_bank_editor, bank_metadata_audit_details,
        )
        bank = get_object_or_404(QuestionBank, pk=bank_id)
        if bank.course_id:
            return TeacherQuestionBankEditorView().put(request, bank.course_id, bank.pk)
        with transaction.atomic():
            bank = lock_question_bank(bank)
            _require_standalone_bank_editor(request.user, bank)
            form = _metadata_question_bank_form(_query_data(request.data), instance=bank)
            if not form.is_valid():
                raise ValidationError(form.errors)
            changed_fields = form.changed_data
            bank = form.save()
            log_action(request, 'question_bank.update', bank, bank_metadata_audit_details(bank, changed_fields))
        return Response({'id': bank.pk, 'name': bank.name, 'description': bank.description, 'visibility': bank.visibility})


@extend_schema(request=dict, responses=dict)
class AdminCourseActionView(AdminView):
    def post(self, request, course_id, action):
        if action == 'update':
            return Response(service.course_row(service.save_course(request, request.data, course_id)))
        if action == 'transfer':
            from apps.accounts.collaboration import transfer_course_owner
            course = get_object_or_404(Course, pk=course_id)
            teacher = get_object_or_404(User, pk=request.data.get('teacher_id'), role=User.Role.TEACHER, is_active=True)
            course = transfer_course_owner(course, request.user, teacher, request.data.get('state_token', ''),
                request=request, keep_previous=request.data.get('keep_previous', True), reason=request.data.get('reason', ''))
            return Response(service.course_row(course))
        if action in ('delete-preview', 'delete'):
            from apps.accounts import admin_recycle
            if action == 'delete-preview':
                return Response(admin_recycle.preview('course', course_id, request.user))
            return Response(admin_recycle.remove(request, 'course', course_id, request.data))
        if action in ('archive', 'restore'):
            if request.data.get('confirmation') != action:
                raise ValidationError('请明确确认课程状态变更。')
            with transaction.atomic():
                course = get_object_or_404(Course.objects.select_for_update(), pk=course_id)
                course.status = Course.Status.ARCHIVED if action == 'archive' else Course.Status.ACTIVE
                course.save(update_fields=['status', 'updated_at'])
                log_action(request, f'course.{action}', course)
            return Response(service.course_row(course))
        raise NotFound()


@extend_schema(responses=dict)
class AdminRecycleView(AdminView):
    def get(self, request):
        from apps.accounts.admin_recycle import entry_row
        from apps.accounts.models import AdminRecycleEntry
        return Response({'items': [entry_row(row) for row in AdminRecycleEntry.objects.filter(restored_at__isnull=True)]})


@extend_schema(request=dict, responses=dict)
class AdminRecycleRestoreView(AdminView):
    def post(self, request, entry_id):
        from apps.accounts.admin_recycle import restore
        return Response(restore(request, entry_id))
