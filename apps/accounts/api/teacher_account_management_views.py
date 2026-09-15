import uuid
from urllib.parse import quote

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Q
from django.http import HttpResponse, QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.experiments.api.deletion_views import TeacherClassDeletionView

from apps.accounts.class_management_services import (
    CLASS_GROUP_STALE_ERROR,
    CourseClassDeletionError,
    delete_course_class_group,
    update_course_class_group,
)
from apps.accounts.course_access import (
    accessible_courses,
    get_accessible_course,
    require_writable_course,
)
from apps.accounts.enrollment_services import (
    copy_class_enrollments,
    enroll_single_student,
    import_legacy_students,
    remove_course_enrollment,
    transfer_course_enrollment,
)
from apps.accounts.forms import (
    ClassGroupForm,
    ClassGroupUpdateForm,
    ClassStudentAnalyticsPolicyForm,
    ManualStudentEnrollmentForm,
    PasswordResetForm,
    StudentBatchUploadForm,
    StudentClassCopyForm,
    StudentCreateForm,
    class_group_state_token,
    class_group_state_token_matches,
)
from apps.accounts.models import (
    ClassGroup,
    ClassStudentAnalyticsPolicy,
    CourseEnrollment,
    StudentImportJob,
    User,
)
from apps.accounts.student_analytics_policy import (
    POLICY_FIELD_NAMES,
    update_class_student_analytics_policy,
)
from apps.accounts.student_import import (
    ParsedStudent,
    StudentImportError,
    build_student_import_workbook,
    parse_student_workbook,
)
from apps.accounts.student_import_jobs import IMPORT_PREVIEW_STALE_ERROR
from apps.accounts.views import _class_queryset, _student_queryset
from apps.accounts.password_services import reset_student_initial_password
from apps.accounts.student_identity import (
    StudentIdentityError, affects_other_courses, identity_state_token, update_student_identity,
)
from apps.api.exceptions import ApiBusinessError
from apps.api.permissions import RolePermission
from apps.audit.services import log_action

from .teacher_account_management_serializers import (
    CourseClassCopyConfirmRequestSerializer,
    CourseClassCopyPreviewRequestSerializer,
    CourseClassCopyPreviewSerializer,
    CourseClassCopyResultSerializer,
    CourseClassDeleteRequestSerializer,
    CourseClassDeleteResponseSerializer,
    CourseClassManagementSerializer,
    CourseClassUpdateRequestSerializer,
    CourseStudentImportConfirmRequestSerializer,
    CourseStudentImportPreviewSerializer,
    CourseStudentImportResultSerializer,
    LegacyStudentImportPreviewSerializer,
    LegacyStudentImportResultSerializer,
    ManualEnrollmentRequestSerializer,
    ManualEnrollmentResponseSerializer,
    MoveEnrollmentRequestSerializer,
    MoveEnrollmentResponseSerializer,
    PasswordResetRequestSerializer,
    RemoveEnrollmentRequestSerializer,
    RemoveEnrollmentResponseSerializer,
    StudentImportPreviewRequestSerializer,
    StudentVisibilityPolicyRequestSerializer,
    StudentVisibilityPolicySerializer,
    TeacherAccountClassCollectionSerializer,
    TeacherAccountClassCreateRequestSerializer,
    TeacherAccountClassCreateResponseSerializer,
    TeacherAccountStudentCollectionSerializer,
    TeacherAccountStudentImportConfirmRequestSerializer,
    TeacherAccountStudentCreateRequestSerializer,
    TeacherAccountStudentCreateResponseSerializer,
    TeacherAccountStudentSerializer,
    StudentIdentityUpdateRequestSerializer,
    StudentIdentityUpdateResponseSerializer,
)


LEGACY_IMPORT_SESSION_KEY = "student_batch_import"


def _form_data(payload, field_names, *, boolean_fields=()):
    data = QueryDict("", mutable=True)
    boolean_fields = frozenset(boolean_fields)
    for field_name in field_names:
        value = payload.get(field_name)
        if field_name in boolean_fields:
            data[field_name] = "on" if value is True else ""
        else:
            data[field_name] = "" if value is None else str(value)
    return data


def _course_payload(course):
    return {
        "id": course.pk,
        "name": course.name,
        "code": course.code,
        "term": course.term,
        "status": course.status,
    }


def _class_option_payload(class_group):
    return {
        "id": class_group.pk,
        "name": class_group.name,
        "academic_year": class_group.academic_year,
        "display_name": str(class_group),
        "is_active": class_group.is_active,
    }


def _student_payload(student):
    return {
        "id": student.pk,
        "student_number": student.student_number,
        "display_name": student.shown_name,
        "username": student.username,
        "class_group": (
            _class_option_payload(student.class_group)
            if student.class_group_id
            else None
        ),
    }


def _source_class_payload(class_group):
    course = class_group.course
    return {
        "id": class_group.pk,
        "course_id": course.pk,
        "course_name": course.name,
        "course_code": course.code,
        "course_term": course.term,
        "name": class_group.name,
        "academic_year": class_group.academic_year,
        "display_name": str(class_group),
    }


def _preview_payload(parsed_students, *, existing_numbers=()):
    existing_numbers = set(existing_numbers)
    return [
        {
            "student_number": item.student_number,
            "display_name": item.display_name,
            "class_id": item.class_id,
            "class_name": item.class_name,
            "account_action": (
                "reuse" if item.student_number in existing_numbers else "create"
            ),
        }
        for item in parsed_students
    ]


def _existing_student_numbers(parsed_students):
    numbers = [item.student_number for item in parsed_students]
    return {
        value
        for student_number, username in User.objects.filter(
            Q(student_number__in=numbers) | Q(username__in=numbers)
        ).values_list("student_number", "username")
        for value in (student_number, username)
        if value in numbers
    }


def _student_import_job_payload(job):
    return {
        "job_id": job.job_id,
        "status": job.status,
        "total_rows": job.total_rows,
        "processed_rows": job.processed_rows,
        "created_accounts": job.created_accounts,
        "enrollments": job.enrollments,
        "progress_stage": job.progress_stage,
        "error_code": job.error_code,
        "error_message": job.error_message,
    }


def _request_ip(request):
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    return (
        forwarded.split(",")[0].strip()
        if forwarded
        else request.META.get("REMOTE_ADDR")
    ) or None


def _require_active_class(class_group, action):
    if not class_group.is_active:
        raise ValidationError(f"目标班级已停用，不能{action}。")


def _course_and_class(user, course_id, class_id):
    course = get_accessible_course(user, course_id)
    class_group = get_object_or_404(course.classes, pk=class_id)
    return course, class_group


def _source_classes(user, course):
    return ClassGroup.objects.filter(
        course__in=accessible_courses(user).exclude(pk=course.pk)
    ).select_related("course").order_by(
        "course__term", "course__code", "academic_year", "name", "pk"
    )


def _class_management_payload(course, class_group, user):
    enrollments = class_group.enrollments.filter(is_active=True).select_related(
        "student", "student__class_group"
    ).order_by("student__student_number", "student__display_name", "pk")
    target_classes = course.classes.filter(is_active=True).order_by(
        "academic_year", "name", "pk"
    )
    source_classes = _source_classes(user, course)
    writable = course.status == course.Status.ACTIVE
    return {
        "course": _course_payload(course),
        "class_group": {
            "id": class_group.pk,
            "name": class_group.name,
            "academic_year": class_group.academic_year,
            "is_active": class_group.is_active,
            "state_token": ClassGroupUpdateForm(instance=class_group).initial[
                "state_token"
            ],
            "detail_url": reverse(
                "courses:class_detail", args=[course.pk, class_group.pk]
            ),
        },
        "enrollments": [
            {
                "enrollment_id": enrollment.pk,
                "student": _student_payload(enrollment.student),
                "identity_state_token": identity_state_token(enrollment.student, course, class_group, user),
                "affects_other_courses": affects_other_courses(enrollment.student, course),
            }
            for enrollment in enrollments
        ],
        "target_classes": [_class_option_payload(item) for item in target_classes],
        "source_classes": [_source_class_payload(item) for item in source_classes],
        "capabilities": {
            "can_update": writable,
            "can_delete": writable,
            "can_add_students": writable and class_group.is_active,
            "can_move_students": writable,
            "can_remove_students": writable,
            "can_reset_passwords": writable,
            "can_edit_students": writable,
            "can_update_visibility": writable and settings.CLASS_CENTER_ENABLED,
        },
        "actions": {
            "detail_url": reverse(
                "courses:class_detail", args=[course.pk, class_group.pk]
            ),
            "classes_url": reverse("courses:classes", args=[course.pk]),
            "add_students_url": reverse(
                "courses:student_add", args=[course.pk, class_group.pk]
            ),
            "visibility_url": reverse(
                "courses:class_student_visibility", args=[course.pk, class_group.pk]
            ),
            "import_template_url": reverse(
                "courses:student_import_template", args=[course.pk, class_group.pk]
            ),
        },
    }


def _policy_payload(policy, *, is_read_only):
    return {
        **{field_name: getattr(policy, field_name) for field_name in POLICY_FIELD_NAMES},
        "is_read_only": is_read_only,
    }


class TeacherAccountManagementApiView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)

    def handle_exception(self, exc):
        if isinstance(exc, StudentIdentityError):
            exc = ApiBusinessError("；".join(exc.messages), code=exc.business_code, fields=exc.fields, status=exc.status_code)
        return super().handle_exception(exc)


class TeacherAccountClassCollectionView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_account_class_list",
        responses=TeacherAccountClassCollectionSerializer,
    )
    def get(self, request):
        classes = _class_queryset(request.user).order_by("academic_year", "name", "pk")
        return Response(
            TeacherAccountClassCollectionSerializer(
                {
                    "items": [
                        {
                            "id": class_group.pk,
                            "name": class_group.name,
                            "academic_year": class_group.academic_year,
                            "is_active": class_group.is_active,
                            "course_id": class_group.course_id,
                            "student_count": class_group.students.filter(
                                role=User.Role.STUDENT
                            ).count(),
                        }
                        for class_group in classes
                    ]
                }
            ).data
        )

    @extend_schema(
        operation_id="teacher_account_class_create",
        request=TeacherAccountClassCreateRequestSerializer,
        responses={201: TeacherAccountClassCreateResponseSerializer},
    )
    def post(self, request):
        data = _form_data(request.data, ("name", "academic_year"))
        form = ClassGroupForm(data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        class_group = form.save(commit=False)
        class_group.owner = request.user
        class_group.save()
        log_action(
            request,
            "class.create",
            class_group,
            {"name": class_group.name, "academic_year": class_group.academic_year},
        )
        return Response(
            TeacherAccountClassCreateResponseSerializer(
                {
                    "class_group": {
                        "id": class_group.pk,
                        "name": class_group.name,
                        "academic_year": class_group.academic_year,
                        "is_active": class_group.is_active,
                        "course_id": class_group.course_id,
                        "student_count": 0,
                    }
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class TeacherAccountStudentCollectionView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_account_student_list",
        responses=TeacherAccountStudentCollectionSerializer,
    )
    def get(self, request):
        classes = _class_queryset(request.user).order_by("academic_year", "name", "pk")
        students = _student_queryset(request.user).order_by(
            "student_number", "display_name", "pk"
        )
        query = request.query_params.get("q", "").strip()
        raw_class_id = request.query_params.get("class_id", "").strip()
        selected_class_id = None
        if query:
            students = students.filter(
                Q(student_number__icontains=query) | Q(display_name__icontains=query)
            )
        if raw_class_id.isdecimal() and classes.filter(pk=raw_class_id).exists():
            selected_class_id = int(raw_class_id)
            students = students.filter(class_group_id=selected_class_id)
        class_options = classes.filter(is_active=True)
        return Response(
            TeacherAccountStudentCollectionSerializer(
                {
                    "items": [_student_payload(student) for student in students],
                    "class_options": [
                        _class_option_payload(class_group)
                        for class_group in class_options
                    ],
                    "query": query,
                    "selected_class_id": selected_class_id,
                }
            ).data
        )

    @extend_schema(
        operation_id="teacher_account_student_create",
        request=TeacherAccountStudentCreateRequestSerializer,
        responses={201: TeacherAccountStudentCreateResponseSerializer},
    )
    def post(self, request):
        data = _form_data(
            request.data,
            ("student_number", "display_name", "class_group"),
        )
        form = StudentCreateForm(data, owner=request.user)
        if not form.is_valid():
            raise ValidationError(form.errors)
        try:
            with transaction.atomic():
                student = form.save()
                log_action(request, "student.create", student, {
                    "student_number": student.student_number,
                    "class": str(student.class_group or ""),
                })
        except IntegrityError as exc:
            number = form.cleaned_data["student_number"]
            if User.objects.filter(student_number=number).exists():
                raise StudentIdentityError("STUDENT_NUMBER_CONFLICT", "该学号已被其他学生账号占用。") from exc
            if User.objects.filter(username=User.normalize_username(number)).exists():
                raise StudentIdentityError("USERNAME_CONFLICT", "该登录用户名已被其他账号占用。") from exc
            raise
        return Response(
            TeacherAccountStudentCreateResponseSerializer(
                {"student": _student_payload(student)}
            ).data,
            status=status.HTTP_201_CREATED,
        )


class TeacherAccountStudentImportPreviewView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_account_student_import_preview",
        request=StudentImportPreviewRequestSerializer,
        responses=LegacyStudentImportPreviewSerializer,
    )
    def post(self, request):
        request.session.pop(LEGACY_IMPORT_SESSION_KEY, None)
        form = StudentBatchUploadForm(request.data, request.FILES)
        if not form.is_valid():
            raise ValidationError(form.errors)
        classes = list(_class_queryset(request.user))
        try:
            preview = parse_student_workbook(form.cleaned_data["file"], classes)
            classes_by_id = {class_group.pk: class_group for class_group in classes}
            for item in preview:
                if not classes_by_id[item.class_id].is_active:
                    raise StudentImportError(
                        f"班级已停用，不能导入学生：{item.class_name}"
                    )
        except StudentImportError as exc:
            raise ValidationError(str(exc)) from exc
        request.session[LEGACY_IMPORT_SESSION_KEY] = [item.__dict__ for item in preview]
        return Response(
            LegacyStudentImportPreviewSerializer(
                {"preview": _preview_payload(preview)}
            ).data
        )


class TeacherAccountStudentImportConfirmView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_account_student_import_confirm",
        request=TeacherAccountStudentImportConfirmRequestSerializer,
        responses=LegacyStudentImportResultSerializer,
    )
    def post(self, request):
        confirmation = TeacherAccountStudentImportConfirmRequestSerializer(data=request.data)
        confirmation.is_valid(raise_exception=True)
        preview = [
            ParsedStudent(**item)
            for item in request.session.get(LEGACY_IMPORT_SESSION_KEY, [])
        ]
        try:
            import_legacy_students(preview, _class_queryset(request.user))
        except (StudentImportError, IntegrityError) as exc:
            raise ValidationError(
                str(exc) if str(exc) else "导入失败，请重新校验名单"
            ) from exc
        request.session.pop(LEGACY_IMPORT_SESSION_KEY, None)
        log_action(request, "student.batch_import", details={"count": len(preview)})
        return Response(LegacyStudentImportResultSerializer({"imported": len(preview)}).data)


class TeacherAccountStudentResetView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_account_student_password_reset",
        request=PasswordResetRequestSerializer,
        responses=TeacherAccountStudentCreateResponseSerializer,
    )
    def post(self, request, student_id):
        student = get_object_or_404(_student_queryset(request.user), pk=student_id)
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student = reset_student_initial_password(request=request, student_id=student.pk)
        return Response(
            TeacherAccountStudentCreateResponseSerializer(
                {"student": _student_payload(student)}
            ).data
        )


class CourseClassManagementView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_management",
        responses=CourseClassManagementSerializer,
    )
    def get(self, request, course_id, class_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        return Response(
            CourseClassManagementSerializer(
                _class_management_payload(course, class_group, request.user)
            ).data
        )

    @extend_schema(
        operation_id="teacher_course_class_management_update",
        request=CourseClassUpdateRequestSerializer,
        responses=CourseClassManagementSerializer,
    )
    def patch(self, request, course_id, class_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        data = _form_data(
            request.data,
            ("name", "academic_year", "is_active", "state_token"),
            boolean_fields=("is_active",),
        )
        saved_class_group, form = update_course_class_group(
            request=request,
            course=course,
            class_group=class_group,
            form_data=data,
        )
        if saved_class_group is None:
            if CLASS_GROUP_STALE_ERROR in form.non_field_errors():
                raise ApiBusinessError(
                    CLASS_GROUP_STALE_ERROR,
                    code="CLASS_STATE_STALE",
                    fields={"__all__": [CLASS_GROUP_STALE_ERROR]},
                    status=409,
                )
            raise ValidationError(form.errors)
        return Response(
            CourseClassManagementSerializer(
                _class_management_payload(course, saved_class_group, request.user)
            ).data
        )


class CourseClassDeleteView(TeacherClassDeletionView):
    pass


class CourseClassManualEnrollmentView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_manual_enrollment",
        request=ManualEnrollmentRequestSerializer,
        responses={201: ManualEnrollmentResponseSerializer},
    )
    def post(self, request, course_id, class_id):
        serializer = ManualEnrollmentRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data.get("confirmation_token"):
            course, class_group = _course_and_class(request.user, course_id, class_id)
            require_writable_course(course)
            _require_active_class(class_group, "新增学生")
        form = ManualStudentEnrollmentForm(data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        student, created_account = enroll_single_student(
            course_id, class_id, form.cleaned_data["student_number"], form.cleaned_data["display_name"],
            request=request, confirmation_token=data.get("confirmation_token"),
        )
        return Response(
            ManualEnrollmentResponseSerializer(
                {
                    "student": _student_payload(student),
                    "created_account": created_account,
                }
            ).data,
            status=status.HTTP_201_CREATED,
        )


class CourseClassCopyPreviewView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_copy_preview",
        request=CourseClassCopyPreviewRequestSerializer,
        responses=CourseClassCopyPreviewSerializer,
    )
    def post(self, request, course_id, class_id):
        course, target_class = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        _require_active_class(target_class, "复制学生")
        source_classes = _source_classes(request.user, course)
        session_key = f"course_student_copy:{course.pk}:{target_class.pk}"
        request.session.pop(session_key, None)
        data = _form_data(request.data, ("source_class",))
        form = StudentClassCopyForm(data, source_classes=source_classes)
        if not form.is_valid():
            raise ValidationError(form.errors)
        source_class = form.cleaned_data["source_class"]
        request.session[session_key] = source_class.pk
        existing_ids = set(
            CourseEnrollment.objects.filter(course=course).values_list(
                "student_id", flat=True
            )
        )
        preview = [
            {
                "student": _student_payload(enrollment.student),
                "skip": enrollment.student_id in existing_ids,
            }
            for enrollment in source_class.enrollments.filter(is_active=True)
            .select_related("student", "student__class_group")
            .order_by("student__student_number", "student__display_name", "pk")
        ]
        return Response(
            CourseClassCopyPreviewSerializer(
                {
                    "source_class": _source_class_payload(source_class),
                    "preview": preview,
                }
            ).data
        )


class CourseClassCopyConfirmView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_copy_confirm",
        request=CourseClassCopyConfirmRequestSerializer,
        responses=CourseClassCopyResultSerializer,
    )
    def post(self, request, course_id, class_id):
        course, target_class = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        _require_active_class(target_class, "复制学生")
        confirmation = CourseClassCopyConfirmRequestSerializer(data=request.data)
        confirmation.is_valid(raise_exception=True)
        source_classes = _source_classes(request.user, course)
        session_key = f"course_student_copy:{course.pk}:{target_class.pk}"
        source_class = source_classes.filter(
            pk=request.session.get(session_key)
        ).first()
        if source_class is None:
            raise ValidationError("复制预览已失效，请重新选择来源班级。")
        try:
            copied, skipped = copy_class_enrollments(
                target_course=course,
                target_class=target_class,
                source_class=source_class,
            )
        except DjangoValidationError as exc:
            raise ValidationError("; ".join(exc.messages)) from exc
        request.session.pop(session_key, None)
        log_action(
            request,
            "student.class_copy",
            target_class,
            {
                "course_id": course.pk,
                "source_course_id": source_class.course_id,
                "source_class_id": source_class.pk,
                "copied": copied,
                "skipped": skipped,
            },
        )
        return Response(
            CourseClassCopyResultSerializer(
                {"copied": copied, "skipped": skipped}
            ).data
        )


class CourseStudentImportPreviewView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_import_preview",
        request=StudentImportPreviewRequestSerializer,
        responses=CourseStudentImportPreviewSerializer,
    )
    def post(self, request, course_id, class_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        _require_active_class(class_group, "新增学生")
        session_key = f"course_student_import:{course.pk}:{class_group.pk}"
        request.session.pop(session_key, None)
        form = StudentBatchUploadForm(request.data, request.FILES)
        if not form.is_valid():
            raise ValidationError(form.errors)
        try:
            preview = parse_student_workbook(
                form.cleaned_data["file"], [class_group], allow_existing=True
            )
        except StudentImportError as exc:
            raise ValidationError(str(exc)) from exc
        preview_id = uuid.uuid4()
        state_token = class_group_state_token(class_group)
        request.session[session_key] = {
            "preview_id": str(preview_id),
            "class_id": class_group.pk,
            "class_state_token": state_token,
            "rows": [item.__dict__ for item in preview],
        }
        return Response(
            CourseStudentImportPreviewSerializer(
                {
                    "preview_id": preview_id,
                    "class_state_token": state_token,
                    "preview": _preview_payload(
                        preview,
                        existing_numbers=_existing_student_numbers(preview),
                    )
                }
            ).data
        )


class CourseStudentImportConfirmView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_import_confirm",
        request=CourseStudentImportConfirmRequestSerializer,
        responses={202: CourseStudentImportResultSerializer},
    )
    def post(self, request, course_id, class_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        _require_active_class(class_group, "新增学生")
        confirmation = CourseStudentImportConfirmRequestSerializer(data=request.data)
        confirmation.is_valid(raise_exception=True)
        preview_id = confirmation.validated_data["preview_id"]
        state_token = confirmation.validated_data["class_state_token"]
        existing_job = StudentImportJob.objects.filter(
            preview_id=preview_id,
            course=course,
            class_group=class_group,
            created_by=request.user,
        ).first()
        if existing_job is not None:
            return Response(
                CourseStudentImportResultSerializer(
                    _student_import_job_payload(existing_job)
                ).data,
                status=status.HTTP_202_ACCEPTED,
            )
        session_key = f"course_student_import:{course.pk}:{class_group.pk}"
        preview_state = request.session.get(session_key, {})
        if (
            not preview_state
            or preview_state.get("preview_id") != str(preview_id)
            or preview_state.get("class_id") != class_group.pk
            or preview_state.get("class_state_token") != state_token
        ):
            raise ValidationError("导入预览已失效，请重新上传文件。")
        if not class_group_state_token_matches(state_token, class_group):
            request.session.pop(session_key, None)
            raise ApiBusinessError(
                IMPORT_PREVIEW_STALE_ERROR,
                code="IMPORT_PREVIEW_STALE",
                status=409,
            )
        rows = preview_state.get("rows", [])
        if not rows:
            raise ValidationError("导入预览已失效，请重新上传文件。")
        job, created = StudentImportJob.objects.get_or_create(
            preview_id=preview_id,
            defaults={
                "course": course,
                "class_group": class_group,
                "created_by": request.user,
                "class_state_token": state_token,
                "rows": rows,
                "total_rows": len(rows),
                "request_ip": _request_ip(request),
            },
        )
        if (
            job.course_id != course.pk
            or job.class_group_id != class_group.pk
            or job.created_by_id != request.user.pk
        ):
            raise ValidationError("导入预览已失效，请重新上传文件。")
        request.session.pop(session_key, None)
        if created:
            log_action(
                request,
                "student.batch_import_queued",
                class_group,
                {
                    "course_id": course.pk,
                    "class_id": class_group.pk,
                    "job_id": str(job.job_id),
                    "count": job.total_rows,
                },
            )
        return Response(
            CourseStudentImportResultSerializer(
                _student_import_job_payload(job)
            ).data,
            status=status.HTTP_202_ACCEPTED,
        )


class CourseStudentImportJobView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_import_job",
        responses=CourseStudentImportResultSerializer,
    )
    def get(self, request, course_id, class_id, job_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        job = get_object_or_404(
            StudentImportJob,
            job_id=job_id,
            course=course,
            class_group=class_group,
        )
        return Response(
            CourseStudentImportResultSerializer(_student_import_job_payload(job)).data
        )


class CourseStudentMoveView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_move",
        request=MoveEnrollmentRequestSerializer,
        responses=MoveEnrollmentResponseSerializer,
    )
    def post(self, request, course_id, class_id, student_id):
        course, source_class = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        enrollment = get_object_or_404(
            CourseEnrollment,
            course=course,
            class_group=source_class,
            student_id=student_id,
            is_active=True,
        )
        target_class = get_object_or_404(
            course.classes, pk=request.data.get("class_group")
        )
        _require_active_class(target_class, "转入学生")
        try:
            enrollment = transfer_course_enrollment(enrollment, target_class)
        except DjangoValidationError as exc:
            raise ValidationError("; ".join(exc.messages)) from exc
        log_action(
            request,
            "student.class_transfer",
            enrollment,
            {
                "course_id": course.pk,
                "from_class_id": source_class.pk,
                "to_class_id": target_class.pk,
            },
        )
        return Response(
            MoveEnrollmentResponseSerializer(
                {"class_group": _class_option_payload(target_class)}
            ).data
        )


class CourseStudentRemoveView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_remove",
        request=RemoveEnrollmentRequestSerializer,
        responses=RemoveEnrollmentResponseSerializer,
    )
    def post(self, request, course_id, class_id, student_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        enrollment = get_object_or_404(
            CourseEnrollment.objects.select_related("student"),
            course=course,
            class_group=class_group,
            student_id=student_id,
            is_active=True,
        )
        if request.data.get("confirmation") != "delete":
            raise ValidationError("请在二次确认后再将学生移出课程。")
        try:
            with transaction.atomic():
                details = remove_course_enrollment(
                    course=course, class_group=class_group, enrollment=enrollment,
                )
                log_action(request, "student.course_remove", enrollment.student, details)
        except DjangoValidationError as exc:
            raise ValidationError("; ".join(exc.messages)) from exc
        return Response(RemoveEnrollmentResponseSerializer({"removed": True}).data)


class CourseStudentIdentityView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_student_identity_update",
        request=StudentIdentityUpdateRequestSerializer,
        responses=StudentIdentityUpdateResponseSerializer,
    )
    def patch(self, request, course_id, class_id, student_id):
        serializer = StudentIdentityUpdateRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        student, course, class_group = update_student_identity(
            request=request, course_id=course_id, class_id=class_id, student_id=student_id,
            **serializer.validated_data,
        )
        return Response(StudentIdentityUpdateResponseSerializer({
            "student": _student_payload(student),
            "identity_state_token": identity_state_token(student, course, class_group, request.user),
            "affects_other_courses": affects_other_courses(student, course),
        }).data)


class CourseStudentResetView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_password_reset",
        request=PasswordResetRequestSerializer,
        responses=TeacherAccountStudentCreateResponseSerializer,
    )
    def post(self, request, course_id, class_id, student_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        require_writable_course(course)
        enrollment = get_object_or_404(
            CourseEnrollment.objects.select_related("student"),
            course=course,
            class_group=class_group,
            student_id=student_id,
            is_active=True,
        )
        serializer = PasswordResetRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrollment.student = reset_student_initial_password(
            request=request, student_id=student_id, course_id=course_id, class_id=class_id,
        )
        return Response(
            TeacherAccountStudentCreateResponseSerializer(
                {"student": _student_payload(enrollment.student)}
            ).data
        )


class CourseStudentVisibilityView(TeacherAccountManagementApiView):
    def _scope(self, user, course_id, class_id):
        if not settings.CLASS_CENTER_ENABLED:
            raise NotFound()
        course, class_group = _course_and_class(user, course_id, class_id)
        policy = get_object_or_404(
            ClassStudentAnalyticsPolicy, class_group=class_group
        )
        return course, class_group, policy

    @extend_schema(
        operation_id="teacher_course_class_student_visibility",
        responses=StudentVisibilityPolicySerializer,
    )
    def get(self, request, course_id, class_id):
        course, _class_group, policy = self._scope(
            request.user, course_id, class_id
        )
        return Response(
            StudentVisibilityPolicySerializer(
                _policy_payload(
                    policy,
                    is_read_only=course.status == course.Status.ARCHIVED,
                )
            ).data
        )

    @extend_schema(
        operation_id="teacher_course_class_student_visibility_update",
        request=StudentVisibilityPolicyRequestSerializer,
        responses=StudentVisibilityPolicySerializer,
    )
    def patch(self, request, course_id, class_id):
        course, class_group, _policy = self._scope(request.user, course_id, class_id)
        data = _form_data(
            request.data,
            POLICY_FIELD_NAMES,
            boolean_fields=set(POLICY_FIELD_NAMES) - {"rank_mode"},
        )
        form = ClassStudentAnalyticsPolicyForm(data)
        if not form.is_valid():
            raise ValidationError(form.errors)
        try:
            policy = update_class_student_analytics_policy(
                class_group=class_group,
                actor=request.user,
                changes=form.cleaned_data,
            )
        except DjangoValidationError as exc:
            if hasattr(exc, "message_dict"):
                raise ValidationError(exc.message_dict) from exc
            raise ValidationError("; ".join(exc.messages)) from exc
        return Response(
            StudentVisibilityPolicySerializer(
                _policy_payload(policy, is_read_only=course.status != course.Status.ACTIVE)
            ).data
        )


class CourseStudentImportTemplateView(TeacherAccountManagementApiView):
    @extend_schema(
        operation_id="teacher_course_class_student_import_template",
        responses={200: OpenApiTypes.BINARY},
    )
    def get(self, request, course_id, class_id):
        course, class_group = _course_and_class(request.user, course_id, class_id)
        _require_active_class(class_group, "新增学生")
        content = build_student_import_workbook(
            [class_group],
            scope_label=(
                f"{course.name}（{course.code} · {course.term}） / {class_group}"
            ),
        )
        response = HttpResponse(
            content,
            content_type=(
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            ),
        )
        encoded_name = quote(f"{course.code}-{class_group.name}-学生导入模板.xlsx")
        response["Content-Disposition"] = (
            "attachment; filename=student_import_template.xlsx; "
            f"filename*=UTF-8''{encoded_name}"
        )
        return response
