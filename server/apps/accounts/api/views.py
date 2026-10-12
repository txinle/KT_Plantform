from django.conf import settings
from django.db import transaction
from django.db.models import Count
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import (
    accessible_courses,
    get_accessible_course,
    lock_course,
    require_course_manager,
    require_writable_course,
)
from apps.accounts.forms import (
    CourseForm,
    CourseSettingsForm,
    course_settings_state_token,
    course_settings_state_token_matches,
)
from apps.accounts.models import Course, DataPurgeRequest, User
from apps.api.permissions import RolePermission
from apps.audit.services import log_action
from apps.analytics.services import execute_purge_request

from .serializers import (
    CourseContextSerializer,
    TeacherCourseCollectionSerializer,
    TeacherCourseCreateRequestSerializer,
    TeacherCourseCreateResponseSerializer,
    TeacherCourseDetailSerializer,
    TeacherCourseArchiveRequestSerializer,
    TeacherCoursePurgeCreateRequestSerializer,
    TeacherCoursePurgeExecuteRequestSerializer,
    TeacherCourseSettingsRequestSerializer,
    TeacherCourseSettingsSerializer,
)


class CourseContextView(APIView):
    @extend_schema(responses=CourseContextSerializer)
    def get(self, request, course_id):
        course = get_object_or_404(Course, pk=course_id)
        if not course.can_manage(request.user):
            raise NotFound()
        return Response(CourseContextSerializer(course, context={"request": request}).data)


def _form_data(payload, *, include_teachers=False):
    data = QueryDict("", mutable=True)
    for field_name in ("name", "code", "term", "description", "state_token"):
        if field_name not in payload:
            continue
        value = payload.get(field_name)
        data[field_name] = "" if value is None else str(value)
    if include_teachers:
        if hasattr(payload, "getlist"):
            values = payload.getlist("teachers")
        else:
            value = payload.get("teachers", [])
            values = value if isinstance(value, (list, tuple)) else [value]
        data.setlist(
            "teachers",
            [str(value) for value in values if value not in (None, "")],
        )
    return data


def _course_summary(course):
    class_count = (
        course.class_count if hasattr(course, "class_count") else course.classes.count()
    )
    bank_count = (
        course.bank_count
        if hasattr(course, "bank_count")
        else course.question_banks.count()
    )
    experiment_count = (
        course.experiment_count
        if hasattr(course, "experiment_count")
        else course.experiments.count()
    )
    return {
        "id": course.pk,
        "name": course.name,
        "code": course.code,
        "term": course.term,
        "description": course.description,
        "owner_name": course.owner.shown_name,
        "status": course.status,
        "status_label": course.get_status_display(),
        "class_count": class_count,
        "bank_count": bank_count,
        "experiment_count": experiment_count,
        "detail_url": reverse("courses:detail", args=[course.pk]),
    }


def _course_detail_payload(course, user):
    class_count = course.classes.count()
    bank_count = course.question_banks.count()
    experiment_count = course.experiments.count()
    student_count = course.enrollments.filter(is_active=True).count()
    recent_experiments = course.experiments.select_related("question_bank")[:5]
    recent_banks = course.question_banks.annotate(
        question_count=Count("questions", distinct=True),
    )[:5]
    return {
        "course": {
            "id": course.pk,
            "name": course.name,
            "code": course.code,
            "term": course.term,
            "description": course.description,
            "owner_name": course.owner.shown_name,
            "status": course.status,
            "status_label": course.get_status_display(),
            "updated_at": course.updated_at,
            "detail_url": reverse("courses:detail", args=[course.pk]),
            "banks_url": reverse("courses:banks", args=[course.pk]),
            "classes_url": reverse("courses:classes", args=[course.pk]),
            "experiments_url": reverse("courses:experiments", args=[course.pk]),
            "data_url": reverse("courses:data", args=[course.pk]),
            "settings_url": reverse("courses:settings", args=[course.pk]),
        },
        "metrics": {
            "class_count": class_count,
            "bank_count": bank_count,
            "experiment_count": experiment_count,
            "student_count": student_count,
        },
        "recent_experiments": [
            {
                "id": experiment.pk,
                "name": experiment.name,
                "question_bank_name": str(experiment.question_bank or ""),
                "status": experiment.status,
                "status_label": experiment.get_status_display(),
                "detail_url": reverse(
                    "courses:experiment_detail", args=[course.pk, experiment.pk]
                ),
            }
            for experiment in recent_experiments
        ],
        "recent_banks": [
            {
                "id": bank.pk,
                "name": bank.name,
                "description": bank.description,
                "question_count": bank.question_count,
                "visibility": bank.visibility,
                "visibility_label": bank.get_visibility_display(),
                "updated_at": bank.updated_at,
                "detail_url": reverse("courses:bank_detail", args=[course.pk, bank.pk]),
            }
            for bank in recent_banks
        ],
        "class_center_enabled": settings.CLASS_CENTER_ENABLED,
        "is_course_owner": (
            user.is_superuser
            or user.role == User.Role.ADMIN
            or course.owner_id == user.id
        ),
    }


def _course_settings_payload(course):
    teachers = list(
        course.teacher_links.order_by("teacher_id").values_list("teacher_id", flat=True)
    )
    teacher_options = User.objects.filter(
        role=User.Role.TEACHER,
        is_active=True,
    ).exclude(pk=course.owner_id).order_by("pk")
    purge_requests = DataPurgeRequest.objects.filter(
        scope_definition__course_id=course.pk
    )[:10]
    return {
        "course": {
            "id": course.pk,
            "name": course.name,
            "code": course.code,
            "term": course.term,
            "description": course.description,
            "status": course.status,
            "status_label": course.get_status_display(),
        },
        "form": {
            "name": course.name,
            "code": course.code,
            "term": course.term,
            "description": course.description,
            "state_token": course_settings_state_token(course),
            "teachers": teachers,
            "teacher_options": [
                {
                    "id": teacher.pk,
                    "display_name": teacher.shown_name,
                    "username": teacher.username,
                }
                for teacher in teacher_options
            ],
        },
        "capabilities": {
            "can_update": course.status == Course.Status.ACTIVE,
            "can_archive": course.status == Course.Status.ACTIVE,
        },
        "archive_url": reverse("teacher-course-archive", args=[course.pk]),
        "purge_request_create_url": reverse(
            "teacher-course-purge-create", args=[course.pk]
        ),
        "purge_requests": [
            {
                "purge_request_id": purge.purge_request_id,
                "preview_session_count": purge.preview_counts.get("sessions", 0),
                "preview_event_count": purge.preview_counts.get("events", 0),
                "status": purge.status,
                "status_label": purge.get_status_display(),
                "execute_url": (
                    reverse(
                        "teacher-course-purge-execute",
                        args=[course.pk, purge.purge_request_id],
                    )
                    if purge.status == DataPurgeRequest.Status.PREVIEW
                    else None
                ),
            }
            for purge in purge_requests
        ],
    }


class TeacherCourseApiView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)


class TeacherCourseCollectionView(TeacherCourseApiView):
    @extend_schema(
        operation_id="teacher_course_list",
        responses=TeacherCourseCollectionSerializer,
    )
    def get(self, request):
        courses = accessible_courses(request.user).annotate(
            class_count=Count("classes", distinct=True),
            bank_count=Count("question_banks", distinct=True),
            experiment_count=Count("experiments", distinct=True),
        )
        payload = {
            "items": [_course_summary(course) for course in courses],
            "create_url": reverse("courses:create"),
        }
        return Response(TeacherCourseCollectionSerializer(payload).data)

    @extend_schema(
        operation_id="teacher_course_create",
        request=TeacherCourseCreateRequestSerializer,
        responses={201: TeacherCourseCreateResponseSerializer},
    )
    def post(self, request):
        form = CourseForm(_form_data(request.data))
        if not form.is_valid():
            raise ValidationError(form.errors)
        course = form.save(commit=False)
        course.owner = request.user
        course.save()
        log_action(
            request,
            "course.create",
            course,
            {"code": course.code, "term": course.term},
        )
        return Response(
            TeacherCourseCreateResponseSerializer(
                {"course": _course_summary(course)}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class TeacherCourseDetailView(TeacherCourseApiView):
    @extend_schema(
        operation_id="teacher_course_detail",
        responses=TeacherCourseDetailSerializer,
    )
    def get(self, request, course_id):
        course = get_accessible_course(request.user, course_id)
        return Response(
            TeacherCourseDetailSerializer(
                _course_detail_payload(course, request.user)
            ).data
        )


class TeacherCourseSettingsView(TeacherCourseApiView):
    @staticmethod
    def _course(user, course_id):
        course = get_accessible_course(user, course_id)
        require_course_manager(course, user)
        return course

    @extend_schema(
        operation_id="teacher_course_settings",
        responses=TeacherCourseSettingsSerializer,
    )
    def get(self, request, course_id):
        course = self._course(request.user, course_id)
        return Response(
            TeacherCourseSettingsSerializer(_course_settings_payload(course)).data
        )

    @extend_schema(
        operation_id="teacher_course_settings_update",
        request=TeacherCourseSettingsRequestSerializer,
        responses=TeacherCourseSettingsSerializer,
    )
    def post(self, request, course_id):
        course = self._course(request.user, course_id)
        data = _form_data(request.data, include_teachers=True)
        form = CourseSettingsForm(data, instance=course)
        if not form.is_valid():
            raise ValidationError(form.errors)
        with transaction.atomic():
            course = lock_course(course)
            require_course_manager(course, request.user)
            require_writable_course(course)
            form = CourseSettingsForm(data, instance=course)
            if not course_settings_state_token_matches(
                data.get("state_token", ""), course
            ):
                form.add_error(None, "课程设置已被其他管理者更新，请刷新后重新提交。")
                raise ValidationError(form.errors)
            if not form.is_valid():
                raise ValidationError(form.errors)
            previous_teacher_ids = set(
                course.teacher_links.values_list("teacher_id", flat=True)
            )
            course = form.save()
            teacher_ids = set(
                form.cleaned_data["teachers"].values_list("pk", flat=True)
            )
            log_action(
                request,
                "course.update",
                course,
                {"course_id": course.pk, "teacher_ids": sorted(teacher_ids)},
            )
            if teacher_ids != previous_teacher_ids:
                log_action(
                    request,
                    "course.collaborators_update",
                    course,
                    {
                        "course_id": course.pk,
                        "added_teacher_ids": sorted(teacher_ids - previous_teacher_ids),
                        "removed_teacher_ids": sorted(previous_teacher_ids - teacher_ids),
                    },
                )
        return Response(
            TeacherCourseSettingsSerializer(_course_settings_payload(course)).data
        )


class TeacherCourseArchiveView(TeacherCourseApiView):
    @extend_schema(
        operation_id="teacher_course_archive",
        request=TeacherCourseArchiveRequestSerializer,
        responses=TeacherCourseSettingsSerializer,
    )
    def post(self, request, course_id):
        serializer = TeacherCourseArchiveRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = get_accessible_course(request.user, course_id)
        require_course_manager(course, request.user)
        with transaction.atomic():
            course = lock_course(course)
            require_course_manager(course, request.user)
            if course.status != Course.Status.ARCHIVED:
                course.status = Course.Status.ARCHIVED
                course.save(update_fields=["status", "updated_at"])
                log_action(request, "course.archive", course)
        return Response(
            TeacherCourseSettingsSerializer(_course_settings_payload(course)).data
        )


class TeacherCoursePurgeCreateView(TeacherCourseApiView):
    @extend_schema(
        operation_id="teacher_course_purge_create",
        request=TeacherCoursePurgeCreateRequestSerializer,
        responses={201: TeacherCourseSettingsSerializer},
    )
    def post(self, request, course_id):
        serializer = TeacherCoursePurgeCreateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = get_accessible_course(request.user, course_id)
        require_course_manager(course, request.user)
        from apps.assessment.models import AssessmentEvent, ExperimentSession

        preview = {
            "sessions": ExperimentSession.objects.filter(
                version__experiment__course=course
            ).count(),
            "events": AssessmentEvent.objects.filter(
                session__version__experiment__course=course
            ).count(),
        }
        DataPurgeRequest.objects.create(
            scope_type="course",
            scope_definition={"course_id": course.pk},
            reason_code=serializer.validated_data["reason_code"],
            requested_by=request.user,
            preview_counts=preview,
        )
        return Response(
            TeacherCourseSettingsSerializer(_course_settings_payload(course)).data,
            status=status.HTTP_201_CREATED,
        )


class TeacherCoursePurgeExecuteView(TeacherCourseApiView):
    @extend_schema(
        operation_id="teacher_course_purge_execute",
        request=TeacherCoursePurgeExecuteRequestSerializer,
        responses=TeacherCourseSettingsSerializer,
    )
    def post(self, request, course_id, purge_request_id):
        serializer = TeacherCoursePurgeExecuteRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        course = get_accessible_course(request.user, course_id)
        require_course_manager(course, request.user)
        purge = get_object_or_404(
            DataPurgeRequest,
            purge_request_id=purge_request_id,
            scope_definition__course_id=course.pk,
        )
        execute_purge_request(purge, executed_by=request.user)
        log_action(
            request,
            "data_purge.execute",
            course,
            {"purge_request_id": str(purge.purge_request_id)},
        )
        return Response(
            TeacherCourseSettingsSerializer(_course_settings_payload(course)).data
        )
