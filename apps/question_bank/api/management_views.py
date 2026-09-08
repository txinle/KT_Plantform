import uuid
from dataclasses import asdict

from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, Max, Q
from django.http import QueryDict
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, OpenApiTypes, extend_schema
from rest_framework import status
from rest_framework.exceptions import ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.course_access import (
    accessible_courses,
    lock_writable_course,
    require_writable_course,
)
from apps.accounts.models import Course, User
from apps.api.pagination import FixedPageNumberPagination
from apps.api.permissions import RolePermission
from apps.audit.services import log_action
from apps.question_bank.access import (
    lock_question_bank,
    lock_writable_bank_content,
    lock_writable_course_bank,
    require_writable_bank_content,
)
from apps.question_bank.audit import bank_metadata_audit_details
from apps.question_bank.course_views import (
    _metadata_question_bank_form,
    _shared_banks_for,
    can_edit_bank_metadata,
)
from apps.question_bank.forms import (
    CourseConceptForm,
    QuestionBankForm,
    QuestionBankVisibilityForm,
    QuestionEditForm,
)
from apps.question_bank.models import Concept, Question, QuestionBank
from apps.question_bank.services.file_reader import read_uploaded_rows, read_uploaded_workbook
from apps.question_bank.services.import_parsing import (
    ParsedKCMapping,
    ParsedKCNode,
    ParsedKCWorkbook,
    ParsedQuestion,
    ParsedQuestionKCRelation,
    ParsedQuestionKCRoot,
    parse_kc_rows,
    parse_kc_workbook,
    parse_question_kc_tree_rows,
    parse_question_rows,
)
from apps.question_bank.services.import_service import (
    copy_shared_bank,
    import_kc_mappings,
    import_kc_workbook,
    import_questions,
)
from apps.question_bank.services.lifecycle import (
    ReferencedQuestionBankError,
    ReferencedQuestionError,
    delete_question,
    delete_question_bank,
)
from apps.question_bank.services.preview_batch import (
    ImportPreviewBatchConflict,
    ImportPreviewBatchService,
)
from apps.question_bank.services.question_kc_import import (
    QuestionKCTreeImportError,
    apply_question_kc_tree_import,
    preview_question_kc_trees,
)
from apps.question_bank.services.question_kc_mapping import project_question_kc_mapping
from apps.question_bank.services.question_readiness import content_issues, readiness_issues, recheck_bank_drafts
from apps.question_bank.services.unified_import import (
    SelectedRowIdsValidationError,
    UnifiedQuestionImportService,
)

from .views import (
    _bank_for_course,
    _batch_conflict_response,
    _course_for_teacher,
    _course_payload,
    _creator_name,
    _session_key,
)
from .management_serializers import (
    QuestionDraftRecheckSerializer,
    ManagementArchivedBankCollectionSerializer,
    ManagementBankEditorRequestSerializer,
    ManagementBankEditorResponseSerializer,
    ManagementBankVisibilityResultSerializer,
    ManagementCourseConceptDeleteResponseSerializer,
    ManagementCourseConceptEditRequestSerializer,
    ManagementCourseConceptEditResponseSerializer,
    ManagementCourseConceptStatusRequestSerializer,
    ManagementDeleteConfirmationSerializer,
    ManagementErrorResponseSerializer,
    ManagementKCImportConfirmRequestSerializer,
    ManagementKCImportPreviewResponseSerializer,
    ManagementQuestionEditorRequestSerializer,
    ManagementQuestionEditorResponseSerializer,
    ManagementQuestionImportConfirmRequestSerializer,
    ManagementQuestionImportConfirmResponseSerializer,
    ManagementQuestionImportPreviewResponseSerializer,
    ManagementQuestionsUpdatedResponseSerializer,
    ManagementSharedCreatorCollectionSerializer,
    ManagementSharedQuestionBankCollectionSerializer,
    ManagementSharedQuestionBankCopySerializer,
    ManagementSharedSourceCourseCollectionSerializer,
    ManagementStandaloneBankCollectionSerializer,
    ManagementStandaloneBankCreateRequestSerializer,
    ManagementStandaloneBankDetailSerializer,
    ManagementStandaloneBankSerializer,
    ManagementStandaloneConceptCollectionSerializer,
    ManagementStandaloneKCImportPreviewResponseSerializer,
    ManagementStandaloneQuestionEditorResponseSerializer,
    ManagementStandaloneVisibilityRequestSerializer,
)
from .serializers import FileImportRequestSerializer


QUESTION_IMPORT_SESSION_PREFIX = "teacher_question_import_api"


def _form_errors(form):
    return {name: list(errors) for name, errors in form.errors.items()}


def _api_error_response(code, message, status_code, *, fields=None):
    error = {"code": code, "message": message}
    if fields:
        error["fields"] = fields
    return Response({"error": error}, status=status_code)


def _form_error_response(form):
    return _api_error_response(
        "validation_error",
        "请修正以下字段。",
        status.HTTP_400_BAD_REQUEST,
        fields=_form_errors(form),
    )


def _query_data(data):
    values = QueryDict("", mutable=True)
    for key, value in data.items():
        if isinstance(value, list):
            values.setlist(key, ["" if item is None else str(item) for item in value])
        elif value is not None:
            values[key] = str(value)
        else:
            values[key] = ""
    return values


def _page_response(paginator, items, extra):
    response = paginator.get_paginated_response(items)
    response.data.update(extra)
    return response


def _require_metadata_editor(user, course, bank):
    if not can_edit_bank_metadata(user, course, bank):
        raise PermissionDenied("只有课程共同管理者或题库所有者可以修改题库元数据。")
    return bank


def _bank_editor_payload(course, bank):
    form = _metadata_question_bank_form(instance=bank)
    return {
        "id": bank.pk,
        "name": bank.name,
        "description": bank.description,
        "visibility": bank.visibility,
        "visibility_choices": [
            {"value": value, "label": label}
            for value, label in form.fields["visibility"].choices
        ],
        "course": _course_payload(course),
        "detail_url": reverse("courses:bank_detail", args=[course.pk, bank.pk]),
    }


def _question_answer_text(question):
    if question.question_type == Question.Type.TRUE_FALSE and isinstance(question.correct_answer, bool):
        return "正确" if question.correct_answer is True else "错误"
    if isinstance(question.correct_answer, list):
        return ",".join(map(str, question.correct_answer))
    return "" if question.correct_answer is None else str(question.correct_answer)


def _question_editor_payload(course, bank, question):
    options = {item.key.lower(): item.text for item in question.options.all()}
    return {
        "course": _course_payload(course),
        "bank": {"id": bank.pk, "name": bank.name},
        "question": {
            "id": question.pk,
            "sequence": question.sequence,
            "code": question.code,
            "text": question.text,
            "question_type": question.question_type,
            "status": question.status,
            "draft_reasons": readiness_issues(question) if question.status == Question.Status.DRAFT else [],
            "teacher_difficulty": (
                format(question.teacher_difficulty, "f")
                if question.teacher_difficulty is not None
                else ""
            ),
            "absolute_tolerance": (
                format(question.absolute_tolerance, "f")
                if question.absolute_tolerance is not None
                else ""
            ),
            "relative_tolerance": (
                format(question.relative_tolerance, "f")
                if question.relative_tolerance is not None
                else ""
            ),
            "explanation": question.explanation,
            "correct_answer_text": _question_answer_text(question),
            "accepted_answers_text": ";".join(question.accepted_answers or []),
            "options": {key: options.get(key, "") for key in "abcdef"},
            "kc_mapping": project_question_kc_mapping(question),
        },
        "question_types": [
            {"value": value, "label": label}
            for value, label in Question.Type.choices
        ],
        "statuses": [
            {"value": value, "label": label}
            for value, label in Question.Status.choices
        ],
        "concepts": [
            {
                "id": concept.pk,
                "kc_code": concept.code,
                "name": concept.name,
                "description": concept.description,
            }
            for concept in course.concepts.filter(status=Concept.Status.ACTIVE).order_by(
                "code"
            )
        ],
        "detail_url": reverse("courses:bank_detail", args=[course.pk, bank.pk]),
    }


def _question_import_session_key(course, bank):
    return f"{QUESTION_IMPORT_SESSION_PREFIX}:{course.pk}:{bank.pk}"


def _standalone_question_import_session_key(bank):
    return f"{QUESTION_IMPORT_SESSION_PREFIX}:standalone:{bank.pk}"


def _standalone_kc_import_session_key(bank):
    return f"teacher_question_kc_import_api:standalone:{bank.pk}"


def _standalone_bank_queryset(user):
    queryset = QuestionBank.objects.filter(course__isnull=True).select_related(
        "created_by", "owner"
    )
    if user.is_superuser or user.role == User.Role.ADMIN:
        return queryset
    return queryset.filter(owner=user)


def _standalone_bank_for_user(user, bank_id):
    return get_object_or_404(_standalone_bank_queryset(user), pk=bank_id)


def _legacy_bank_for_user(user, bank_id):
    queryset = QuestionBank.objects.all()
    if not (user.is_superuser or user.role == User.Role.ADMIN):
        queryset = queryset.filter(
            Q(owner=user) | Q(course__in=accessible_courses(user))
        )
    return get_object_or_404(queryset.select_related("course"), pk=bank_id)


def _canonical_course_bank_redirect(request, bank):
    destination = request.query_params.get("destination", "detail")
    if destination == "detail":
        return reverse("courses:bank_detail", args=[bank.course_id, bank.pk])
    if destination == "question_import":
        return reverse("courses:question_import", args=[bank.course_id, bank.pk])
    if destination == "kc_import":
        return reverse("courses:kc_import", args=[bank.course_id, bank.pk])
    if destination == "question_editor":
        try:
            question_id = int(request.query_params.get("question_id", ""))
        except (TypeError, ValueError):
            raise ValidationError(
                {"question_id": "编辑题目入口需要当前题库中的题目编号。"}
            ) from None
        question = get_object_or_404(bank.questions, pk=question_id)
        return reverse(
            "courses:question_edit", args=[bank.course_id, bank.pk, question.pk]
        )
    raise ValidationError({"destination": "不支持的题库入口。"})


def _can_edit_standalone_bank(user, bank):
    return bool(
        user.is_superuser
        or user.role == User.Role.ADMIN
        or bank.owner_id == user.pk
    )


def _require_standalone_bank_editor(user, bank):
    if not _can_edit_standalone_bank(user, bank):
        raise PermissionDenied("只有题库所有者可以修改独立题库。")
    return bank


def _standalone_bank_payload(bank):
    return {
        "id": bank.pk,
        "name": bank.name,
        "description": bank.description,
        "visibility": bank.visibility,
        "visibility_label": bank.get_visibility_display(),
        "question_count": (
            bank.question_count
            if hasattr(bank, "question_count")
            else bank.questions.count()
        ),
        "detail_url": reverse("question_bank:bank_detail", args=[bank.pk]),
        "question_import_url": reverse("question_bank:question_import", args=[bank.pk]),
        "kc_import_url": reverse("question_bank:kc_import", args=[bank.pk]),
    }


def _standalone_question_editor_payload(bank, question):
    options = {item.key.lower(): item.text for item in question.options.all()}
    return {
        "bank": {"id": bank.pk, "name": bank.name},
        "question": {
            "id": question.pk,
            "sequence": question.sequence,
            "code": question.code,
            "text": question.text,
            "question_type": question.question_type,
            "status": question.status,
            "draft_reasons": readiness_issues(question) if question.status == Question.Status.DRAFT else [],
            "teacher_difficulty": (
                format(question.teacher_difficulty, "f")
                if question.teacher_difficulty is not None
                else ""
            ),
            "absolute_tolerance": (
                format(question.absolute_tolerance, "f")
                if question.absolute_tolerance is not None
                else ""
            ),
            "relative_tolerance": (
                format(question.relative_tolerance, "f")
                if question.relative_tolerance is not None
                else ""
            ),
            "explanation": question.explanation,
            "correct_answer_text": _question_answer_text(question),
            "accepted_answers_text": ";".join(question.accepted_answers or []),
            "options": {key: options.get(key, "") for key in "abcdef"},
        },
        "question_types": [
            {"value": value, "label": label}
            for value, label in Question.Type.choices
        ],
        "statuses": [
            {"value": value, "label": label}
            for value, label in Question.Status.choices
        ],
        "concepts": [],
        "detail_url": reverse("question_bank:bank_detail", args=[bank.pk]),
    }


def _standalone_question_payload(bank, question):
    return {
        "id": question.pk,
        "sequence": question.sequence,
        "code": question.code,
        "text": question.text,
        "question_type": question.question_type,
        "question_type_label": question.get_question_type_display(),
        "status": question.status,
        "draft_reasons": readiness_issues(question) if question.status == Question.Status.DRAFT else [],
        "status_label": question.get_status_display(),
        "teacher_difficulty": (
            format(question.teacher_difficulty, "f")
            if question.teacher_difficulty is not None
            else None
        ),
        "kc_mapping": project_question_kc_mapping(question),
        "edit_url": reverse(
            "question_bank:question_edit", args=[bank.pk, question.pk]
        ),
    }


def _question_import_preview_row(row):
    issues = content_issues(asdict(row))
    return {
        "classification": "draft" if issues else "ready",
        "draft_reasons": issues,
        "errors": issues,
        "sequence": row.sequence,
        "text": row.text,
        "question_type": row.question_type,
        "question_type_label": Question.Type(row.question_type).label,
        "option_count": len(row.options),
        "teacher_difficulty": row.teacher_difficulty,
    }


def _kc_upload_payload(bank, uploaded):
    if uploaded.name.lower().endswith(".xlsx"):
        sheets = read_uploaded_workbook(uploaded)
        if {"KC节点", "题目映射", "题目KC关系"}.issubset(sheets):
            workbook = parse_kc_workbook(sheets)
            stable_codes = {item.stable_question_code for item in workbook.roots}
            existing_codes = set(
                bank.questions.filter(
                    course_identity__stable_code__in=stable_codes
                ).values_list("course_identity__stable_code", flat=True)
            )
            missing_codes = sorted(stable_codes - existing_codes)
            if missing_codes:
                raise ValueError(f"题库中不存在稳定题号：{missing_codes}")
            return {
                "mode": "workbook",
                "payload": asdict(workbook),
                "preview": {
                    "mode": "workbook",
                    "confirmable": True,
                    "nodes": [asdict(item) for item in workbook.nodes],
                    "questions": [
                        {
                            "stable_question_code": item.stable_question_code,
                            "root_kc_code": item.primary_kc_code,
                        }
                        for item in workbook.roots
                    ],
                },
            }
        uploaded.seek(0)

    rows = read_uploaded_rows(uploaded)
    headers = tuple(str(key).strip() for key in (rows[0].keys() if rows else ()))
    if headers == ("题目序号", "一级知识点", "二级知识点", "三级知识点"):
        preview = preview_question_kc_trees(
            bank=bank,
            parsed_rows=parse_question_kc_tree_rows(rows),
        )
        return {
            "mode": "tree_v2",
            "payload": preview,
            "preview": {"mode": "tree_v2", "confirmable": True, **preview},
        }

    mappings = parse_kc_rows(rows)
    missing = [
        item.sequence
        for item in mappings
        if not bank.questions.filter(sequence=item.sequence).exists()
    ]
    if missing:
        raise ValueError(f"题库中不存在题目序号：{missing}")
    return {
        "mode": "legacy",
        "payload": [asdict(item) for item in mappings],
        "preview": {
            "mode": "legacy",
            "confirmable": True,
            "rows": [
                {
                    "sequence": item.sequence,
                    "primary": item.primary,
                    "secondary": item.secondary,
                }
                for item in mappings
            ],
        },
    }


def _apply_kc_upload(bank, actor, payload):
    mode = payload.get("mode")
    if mode == "tree_v2":
        return apply_question_kc_tree_import(bank=bank, payload=payload["payload"])
    if mode == "workbook":
        stored = payload["payload"]
        result = import_kc_workbook(
            bank,
            ParsedKCWorkbook(
                nodes=[ParsedKCNode(**item) for item in stored.get("nodes", [])],
                roots=[
                    ParsedQuestionKCRoot(**item)
                    for item in stored.get("roots", [])
                ],
                relations=[
                    ParsedQuestionKCRelation(**item)
                    for item in stored.get("relations", [])
                ],
            ),
        )
        return {"questions_updated": result["questions_updated"]}
    if mode == "legacy":
        mappings = [ParsedKCMapping(**item) for item in payload.get("payload", [])]
        return {"questions_updated": import_kc_mappings(bank, actor, mappings)}
    raise ValueError("导入预览类型无效，请重新上传文件。")


class TeacherQuestionBankManagementApiView(APIView):
    permission_classes = (RolePermission,)
    allowed_roles = (User.Role.TEACHER,)


class TeacherArchivedQuestionBankCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_archived_list",
        request=None,
        responses=ManagementArchivedBankCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        banks = (
            course.question_banks.filter(visibility=QuestionBank.Visibility.ARCHIVED)
            .select_related("created_by", "owner", "course__owner")
            .annotate(question_count=Count("questions"))
            .order_by("-updated_at", "pk")
        )
        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(banks, request, view=self)
        items = [
            {
                "id": bank.pk,
                "name": bank.name,
                "description": bank.description,
                "question_count": bank.question_count,
                "created_by_name": _creator_name(bank),
                "updated_at": bank.updated_at,
                "detail_url": reverse("courses:bank_detail", args=[course.pk, bank.pk]),
                "can_edit_metadata": can_edit_bank_metadata(request.user, course, bank),
            }
            for bank in page
        ]
        return _page_response(paginator, items, {"course": _course_payload(course)})


class TeacherQuestionBankEditorView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_editor_retrieve",
        request=None,
        responses=ManagementBankEditorResponseSerializer,
    )
    def get(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        _require_metadata_editor(request.user, course, bank)
        return Response(_bank_editor_payload(course, bank))

    @extend_schema(
        operation_id="teacher_question_bank_editor_update",
        request=ManagementBankEditorRequestSerializer,
        responses={200: ManagementBankEditorResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def put(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank)
            _require_metadata_editor(request.user, course, bank)
            form = _metadata_question_bank_form(_query_data(request.data), instance=bank)
            if not form.is_valid():
                return _form_error_response(form)
            changed_fields = form.changed_data
            bank = form.save()
            log_action(
                request,
                "question_bank.update",
                bank,
                bank_metadata_audit_details(bank, changed_fields),
            )
        return Response(_bank_editor_payload(course, bank))


class TeacherQuestionBankArchiveView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_archive",
        request=None,
        responses=ManagementBankVisibilityResultSerializer,
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank)
            _require_metadata_editor(request.user, course, bank)
            if bank.visibility != QuestionBank.Visibility.ARCHIVED:
                previous_visibility = bank.visibility
                bank.visibility = QuestionBank.Visibility.ARCHIVED
                bank.save(update_fields=["visibility", "updated_at"])
                log_action(
                    request,
                    "question_bank.archive",
                    bank,
                    {
                        "bank_id": bank.pk,
                        "course_id": course.pk,
                        "previous_visibility": previous_visibility,
                    },
                )
        return Response({"id": bank.pk, "visibility": bank.visibility})


class TeacherQuestionBankRestoreView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_restore",
        request=None,
        responses=ManagementBankVisibilityResultSerializer,
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank)
            _require_metadata_editor(request.user, course, bank)
            if bank.visibility == QuestionBank.Visibility.ARCHIVED:
                bank.visibility = QuestionBank.Visibility.PRIVATE
                bank.save(update_fields=["visibility", "updated_at"])
                log_action(
                    request,
                    "question_bank.restore",
                    bank,
                    {"bank_id": bank.pk, "course_id": course.pk},
                )
        return Response({"id": bank.pk, "visibility": bank.visibility})


class TeacherQuestionBankDeleteView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_delete",
        request=ManagementDeleteConfirmationSerializer,
        responses={204: None, 400: ManagementErrorResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def delete(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        if request.data.get("confirmation") != "delete":
            raise ValidationError({"confirmation": "请在二次确认后再永久删除题库。"})
        try:
            with transaction.atomic():
                course, bank = lock_writable_course_bank(course, bank)
                _require_metadata_editor(request.user, course, bank)
                details = delete_question_bank(bank)
                log_action(request, "question_bank.delete", details=details)
        except ReferencedQuestionBankError:
            return _api_error_response(
                "question_bank_referenced",
                "题库已被实验或发布快照引用，不能永久删除。请保留并归档，或先处理无研究数据的草稿实验。",
                status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeacherQuestionDeleteView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_question_delete",
        request=ManagementDeleteConfirmationSerializer,
        responses={204: None, 400: ManagementErrorResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def delete(self, request, course_id, bank_id, question_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        if request.data.get("confirmation") != "delete":
            raise ValidationError({"confirmation": "请在二次确认后再永久删除题目。"})
        try:
            with transaction.atomic():
                course, bank = lock_writable_course_bank(course, bank, content=True)
                question = get_object_or_404(
                    bank.questions.select_for_update().select_related("bank"),
                    pk=question_id,
                )
                details = delete_question(question)
                log_action(request, "question.delete", details=details)
        except ReferencedQuestionError:
            return _api_error_response(
                "question_referenced",
                "题目已被实验或发布快照引用，不能永久删除。请保留题目，或先处理无研究数据的草稿实验。",
                status.HTTP_409_CONFLICT,
            )
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeacherQuestionDraftRecheckView(TeacherQuestionBankManagementApiView):
    @extend_schema(request=None, responses=QuestionDraftRecheckSerializer)
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank, content=True)
            if not course.can_manage(request.user, lock_teacher_link=True):
                raise PermissionDenied("当前账号不能修改该课程题库。")
            result = recheck_bank_drafts(bank)
            log_action(request, "question.readiness_recheck", bank, {"course_id": course.pk, **result})
        return Response(QuestionDraftRecheckSerializer(result).data)


class TeacherStandaloneQuestionDraftRecheckView(TeacherQuestionBankManagementApiView):
    @extend_schema(request=None, responses=QuestionDraftRecheckSerializer)
    def post(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        with transaction.atomic():
            bank = lock_writable_bank_content(bank)
            _require_standalone_bank_editor(request.user, bank)
            if bank.course_id is not None:
                raise PermissionDenied("该题库已不属于独立题库。")
            result = recheck_bank_drafts(bank)
            log_action(request, "question.readiness_recheck", bank, result)
        return Response(QuestionDraftRecheckSerializer(result).data)


class TeacherQuestionEditorView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_question_editor_retrieve",
        request=None,
        responses=ManagementQuestionEditorResponseSerializer,
    )
    def get(self, request, course_id, bank_id, question_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        require_writable_bank_content(bank)
        question = get_object_or_404(
            bank.questions.prefetch_related("options", "concept_links__concept"),
            pk=question_id,
        )
        return Response(_question_editor_payload(course, bank, question))

    @extend_schema(
        operation_id="teacher_question_bank_question_editor_update",
        request=ManagementQuestionEditorRequestSerializer,
        responses={200: ManagementQuestionEditorResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def put(self, request, course_id, bank_id, question_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank, content=True)
            question = get_object_or_404(
                bank.questions.select_for_update().prefetch_related("options"),
                pk=question_id,
            )
            form = QuestionEditForm(
                _query_data(request.data), instance=question, course=course
            )
            if not form.is_valid():
                return _form_error_response(form)
            question = form.save()
            log_action(request, "question.update", question, {"course_id": course.pk})
        question = bank.questions.prefetch_related("options", "concept_links__concept").get(
            pk=question.pk
        )
        return Response(_question_editor_payload(course, bank, question))


class TeacherQuestionImportPreviewView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_question_import_preview",
        request=FileImportRequestSerializer,
        responses={200: ManagementQuestionImportPreviewResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        require_writable_course(course)
        require_writable_bank_content(bank)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 Excel、CSV 或 Word 文件。"})
        try:
            result = UnifiedQuestionImportService.preview(
                bank=bank, actor=request.user, session_key=_session_key(request), uploaded=uploaded,
            )
        except ValueError as error:
            raise ValidationError({"file": str(error)}) from error
        return Response(result)


class TeacherQuestionImportConfirmView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_question_import_confirm",
        request=ManagementQuestionImportConfirmRequestSerializer,
        responses={201: ManagementQuestionImportConfirmResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        try:
            result = UnifiedQuestionImportService.confirm(
                bank=bank, actor=request.user, session_key=_session_key(request),
                preview_id=request.data.get("preview_id"),
                selected_row_ids=(
                    request.data.get("selected_row_ids")
                    if "selected_row_ids" in request.data
                    else None
                ),
                audit_request=request,
            )
        except SelectedRowIdsValidationError as error:
            raise ValidationError({"selected_row_ids": str(error)}) from error
        except ImportPreviewBatchConflict as error:
            return _api_error_response(
                error.code, str(error), status.HTTP_409_CONFLICT,
            )
        return Response(result, status=status.HTTP_201_CREATED)


class TeacherQuestionKCImportWorkflowPreviewView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_kc_import_preview",
        request=FileImportRequestSerializer,
        responses={200: ManagementKCImportPreviewResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        require_writable_course(course)
        require_writable_bank_content(bank)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 CSV 或 Excel 文件。"})
        try:
            raw = uploaded.read()
            uploaded.seek(0)
            payload = _kc_upload_payload(bank, uploaded)
        except (ValueError, QuestionKCTreeImportError) as error:
            return _api_error_response(
                getattr(error, "code", "invalid_question_kc_file"),
                str(error),
                status.HTTP_400_BAD_REQUEST,
            )
        batch = ImportPreviewBatchService.create(
            kind="question_kc",
            course=course,
            bank=bank,
            actor=request.user,
            session_key=_session_key(request),
            file_bytes=raw,
            payload=payload,
            summary={"mode": payload["mode"]},
        )
        return Response({"batch_id": str(batch.pk), "preview": payload["preview"]})


class TeacherQuestionKCImportWorkflowConfirmView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_kc_import_confirm",
        request=ManagementKCImportConfirmRequestSerializer,
        responses={200: ManagementQuestionsUpdatedResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        try:
            batch, result = ImportPreviewBatchService.confirm(
                batch_id=request.data.get("batch_id"),
                kind="question_kc",
                course=course,
                bank=bank,
                actor=request.user,
                session_key=_session_key(request),
                apply=lambda payload: _apply_kc_upload(bank, request.user, payload),
            )
        except ImportPreviewBatchConflict as error:
            conflict = _batch_conflict_response(error)
            return _api_error_response(
                conflict.data["code"], conflict.data["detail"], conflict.status_code
            )
        except (ValueError, QuestionKCTreeImportError) as error:
            return _api_error_response(
                getattr(error, "code", "question_kc_import_stale"),
                str(error),
                status.HTTP_409_CONFLICT,
            )
        log_action(
            request,
            "kc.import",
            bank,
            {"course_id": course.pk, "batch_id": str(batch.pk), **result},
        )
        return Response(result)


class TeacherSharedSourceCourseCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_shared_source_list",
        request=None,
        responses=ManagementSharedSourceCourseCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        source_courses = (
            Course.objects.filter(question_banks__in=_shared_banks_for(course))
            .annotate(
                shared_bank_count=Count(
                    "question_banks",
                    filter=Q(question_banks__visibility=QuestionBank.Visibility.SHARED),
                    distinct=True,
                ),
                shared_creator_count=Count(
                    "question_banks__created_by",
                    filter=Q(question_banks__visibility=QuestionBank.Visibility.SHARED),
                    distinct=True,
                ),
            )
            .distinct()
            .order_by("code", "term", "name", "pk")
        )
        return Response(
            {
                "course": _course_payload(course),
                "items": [
                    {
                        "id": source.pk,
                        "name": source.name,
                        "code": source.code,
                        "term": source.term,
                        "shared_bank_count": source.shared_bank_count,
                        "shared_creator_count": source.shared_creator_count,
                    }
                    for source in source_courses
                ],
            }
        )


def _shared_source_course(course, source_course_id):
    return get_object_or_404(
        Course.objects.filter(question_banks__in=_shared_banks_for(course)).distinct(),
        pk=source_course_id,
    )


def _shared_creator(source_course, creator_id):
    return get_object_or_404(
        User.objects.filter(
            created_question_banks__course=source_course,
            created_question_banks__visibility=QuestionBank.Visibility.SHARED,
        ).distinct(),
        pk=creator_id,
    )


class TeacherSharedCreatorCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_shared_creator_list",
        request=None,
        responses=ManagementSharedCreatorCollectionSerializer,
    )
    def get(self, request, course_id, source_course_id):
        course = _course_for_teacher(request.user, course_id)
        source_course = _shared_source_course(course, source_course_id)
        creators = (
            User.objects.filter(
                created_question_banks__course=source_course,
                created_question_banks__visibility=QuestionBank.Visibility.SHARED,
            )
            .annotate(
                shared_bank_count=Count(
                    "created_question_banks",
                    filter=Q(
                        created_question_banks__course=source_course,
                        created_question_banks__visibility=QuestionBank.Visibility.SHARED,
                    ),
                )
            )
            .order_by("display_name", "username", "pk")
        )
        return Response(
            {
                "course": _course_payload(course),
                "source_course": _course_payload(source_course),
                "items": [
                    {
                        "id": creator.pk,
                        "name": creator.shown_name,
                        "username": creator.username,
                        "shared_bank_count": creator.shared_bank_count,
                    }
                    for creator in creators
                ],
            }
        )


class TeacherSharedQuestionBankCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_shared_bank_list",
        request=None,
        responses=ManagementSharedQuestionBankCollectionSerializer,
    )
    def get(self, request, course_id, source_course_id, creator_id):
        course = _course_for_teacher(request.user, course_id)
        source_course = _shared_source_course(course, source_course_id)
        creator = _shared_creator(source_course, creator_id)
        banks = (
            _shared_banks_for(course)
            .filter(course=source_course, created_by=creator)
            .annotate(question_count=Count("questions"))
            .order_by("name", "pk")
        )
        return Response(
            {
                "course": _course_payload(course),
                "source_course": _course_payload(source_course),
                "creator": {
                    "id": creator.pk,
                    "name": creator.shown_name,
                    "username": creator.username,
                },
                "items": [
                    {
                        "id": bank.pk,
                        "name": bank.name,
                        "description": bank.description,
                        "question_count": bank.question_count,
                    }
                    for bank in banks
                ],
            }
        )


class TeacherSharedQuestionBankCopyView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_question_bank_shared_bank_copy",
        request=None,
        responses=ManagementSharedQuestionBankCopySerializer,
    )
    def post(self, request, course_id, source_course_id, creator_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        source_course = _shared_source_course(course, source_course_id)
        creator = _shared_creator(source_course, creator_id)
        source = get_object_or_404(
            _shared_banks_for(course).filter(
                course=source_course,
                created_by=creator,
            ),
            pk=bank_id,
        )
        with transaction.atomic():
            course = lock_writable_course(course)
            bank = copy_shared_bank(source, course, request.user)
            log_action(
                request,
                "question_bank.copy",
                bank,
                {"source_bank_id": source.pk, "course_id": course.pk},
            )
        return Response(
            {
                "id": bank.pk,
                "name": bank.name,
                "detail_url": reverse("courses:bank_detail", args=[course.pk, bank.pk]),
            },
            status=status.HTTP_201_CREATED,
        )


class TeacherCourseConceptEditorView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        request=ManagementCourseConceptStatusRequestSerializer,
        responses=ManagementCourseConceptDeleteResponseSerializer,
    )
    def patch(self, request, course_id, concept_id):
        course = _course_for_teacher(request.user, course_id)
        data = ManagementCourseConceptStatusRequestSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        with transaction.atomic():
            course = lock_writable_course(course)
            if not course.can_manage(request.user, lock_teacher_link=True):
                raise PermissionDenied("当前账号不能修改该课程 KC。")
            concept = get_object_or_404(course.concepts.select_for_update(), pk=concept_id)
            target = data.validated_data["status"]
            if concept.status != target:
                previous = concept.status
                concept.status = target
                concept.save(update_fields=["status"])
                log_action(request, "concept.restore" if target == Concept.Status.ACTIVE else "concept.archive", concept, {
                    "course_id": course.pk, "kc_code": concept.code,
                    "previous_status": previous, "status": target,
                })
        return Response({"id": concept.pk, "status": concept.status})

    @extend_schema(
        operation_id="teacher_question_bank_course_concept_update",
        request=ManagementCourseConceptEditRequestSerializer,
        responses={200: ManagementCourseConceptEditResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def put(self, request, course_id, concept_id):
        course = _course_for_teacher(request.user, course_id)
        with transaction.atomic():
            course = lock_writable_course(course)
            concept = get_object_or_404(course.concepts.select_for_update(), pk=concept_id)
            old_name = concept.name
            form = CourseConceptForm(_query_data(request.data), course=course, instance=concept)
            if not form.is_valid():
                return _form_error_response(form)
            concept = form.save()
            log_action(
                request,
                "concept.rename",
                concept,
                {"course_id": course.pk, "old_name": old_name, "new_name": concept.name},
            )
        return Response(
            {
                "id": concept.pk,
                "kc_code": concept.code,
                "name": concept.name,
                "description": concept.description,
                "status": concept.status,
            }
        )

    @extend_schema(
        operation_id="teacher_question_bank_course_concept_delete",
        request=None,
        responses={204: None, 200: ManagementCourseConceptDeleteResponseSerializer},
    )
    def delete(self, request, course_id, concept_id):
        course = _course_for_teacher(request.user, course_id)
        with transaction.atomic():
            course = lock_writable_course(course)
            concept = get_object_or_404(course.concepts.select_for_update(), pk=concept_id)
            from apps.experiments.models import QuestionSnapshot

            snapshot_referenced = any(
                snapshot.root_kc_code == concept.code
                or any(
                    node.get("kc_code") == concept.code
                    for node in (snapshot.kc_mapping_snapshot or {}).get("nodes", [])
                )
                for snapshot in QuestionSnapshot.objects.filter(
                    version__experiment__course=course,
                ).only("root_kc_code", "kc_mapping_snapshot")
            )
            if (
                concept.question_links.exists()
                or concept.experiment_sampling_rules.exists()
                or snapshot_referenced
            ):
                concept.status = Concept.Status.ARCHIVED
                concept.save(update_fields=["status"])
                log_action(
                    request,
                    "concept.archive",
                    concept,
                    {
                        "course_id": course.pk,
                        "name": concept.name,
                        "kc_code": concept.code,
                    },
                )
                return Response({"id": concept.pk, "status": concept.status})
            name = concept.name
            log_action(request, "concept.delete", concept, {"course_id": course.pk, "name": name})
            concept.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class TeacherStandaloneQuestionBankCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_list",
        request=None,
        responses=ManagementStandaloneBankCollectionSerializer,
    )
    def get(self, request):
        if accessible_courses(request.user).filter(
            status=Course.Status.ACTIVE
        ).exists():
            return Response({"redirect_url": reverse("courses:list")})
        banks = (
            _standalone_bank_queryset(request.user)
            .annotate(question_count=Count("questions"))
            .order_by("-updated_at", "pk")
        )
        shared_banks = (
            QuestionBank.objects.filter(visibility=QuestionBank.Visibility.SHARED)
            .exclude(owner=request.user)
            .select_related("created_by", "owner", "course__owner")
            .annotate(question_count=Count("questions"))
            .order_by("name", "pk")
        )
        return Response(
            {
                "banks": [_standalone_bank_payload(bank) for bank in banks],
                "shared_banks": [
                    {
                        "id": bank.pk,
                        "name": bank.name,
                        "description": bank.description,
                        "question_count": bank.question_count,
                        "created_by_name": _creator_name(bank),
                    }
                    for bank in shared_banks
                ],
                "visibility_choices": [
                    {"value": value, "label": label}
                    for value, label in QuestionBank.Visibility.choices
                ],
            }
        )

    @extend_schema(
        operation_id="teacher_standalone_question_bank_create",
        request=ManagementStandaloneBankCreateRequestSerializer,
        responses={201: ManagementStandaloneBankSerializer, 400: ManagementErrorResponseSerializer},
    )
    def post(self, request):
        form = QuestionBankForm(_query_data(request.data))
        if not form.is_valid():
            return _form_error_response(form)
        with transaction.atomic():
            bank = form.save(commit=False)
            bank.owner = request.user
            bank.created_by = request.user
            bank.save()
        return Response(
            _standalone_bank_payload(bank), status=status.HTTP_201_CREATED
        )


class TeacherStandaloneQuestionBankDetailView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_resolve",
        request=None,
        parameters=[
            OpenApiParameter(
                name="destination",
                type=OpenApiTypes.STR,
                enum=("detail", "question_import", "kc_import", "question_editor"),
                required=False,
                description="Legacy standalone URL action to resolve for a course-backed bank.",
            ),
            OpenApiParameter(
                name="question_id",
                type=OpenApiTypes.INT,
                required=False,
                description="Required only when destination is question_editor.",
            ),
        ],
        responses=ManagementStandaloneBankDetailSerializer,
    )
    def get(self, request, bank_id):
        bank = _legacy_bank_for_user(request.user, bank_id)
        if bank.course_id:
            return Response(
                {
                    "redirect_url": _canonical_course_bank_redirect(request, bank)
                }
            )
        questions = bank.questions.prefetch_related("options", "concept_links__concept")
        can_edit_metadata = _can_edit_standalone_bank(request.user, bank)
        return Response(
            {
                "bank": _standalone_bank_payload(bank),
                "can_edit_metadata": can_edit_metadata,
                "can_edit_content": (
                    can_edit_metadata
                    and bank.visibility != QuestionBank.Visibility.ARCHIVED
                ),
                "questions": [
                    _standalone_question_payload(bank, question)
                    for question in questions
                ],
            }
        )


class TeacherStandaloneQuestionBankVisibilityView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_visibility_update",
        request=ManagementStandaloneVisibilityRequestSerializer,
        responses={200: ManagementStandaloneBankSerializer, 400: ManagementErrorResponseSerializer},
    )
    def put(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        with transaction.atomic():
            bank = lock_question_bank(bank)
            _require_standalone_bank_editor(request.user, bank)
            form = QuestionBankVisibilityForm(_query_data(request.data), instance=bank)
            if not form.is_valid():
                return _form_error_response(form)
            changed_fields = form.changed_data
            bank = form.save()
            log_action(
                request,
                "question_bank.visibility",
                bank,
                bank_metadata_audit_details(bank, changed_fields),
            )
        return Response(_standalone_bank_payload(bank))


class TeacherStandaloneQuestionEditorView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_question_editor_retrieve",
        request=None,
        responses=ManagementStandaloneQuestionEditorResponseSerializer,
    )
    def get(self, request, bank_id, question_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        require_writable_bank_content(bank)
        question = get_object_or_404(
            bank.questions.prefetch_related("options", "concept_links__concept"),
            pk=question_id,
        )
        return Response(_standalone_question_editor_payload(bank, question))

    @extend_schema(
        operation_id="teacher_standalone_question_bank_question_editor_update",
        request=ManagementQuestionEditorRequestSerializer,
        responses={200: ManagementStandaloneQuestionEditorResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def put(self, request, bank_id, question_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        with transaction.atomic():
            bank = lock_writable_bank_content(bank)
            question = get_object_or_404(
                bank.questions.select_for_update().prefetch_related("options"),
                pk=question_id,
            )
            form = QuestionEditForm(_query_data(request.data), instance=question)
            if not form.is_valid():
                return _form_error_response(form)
            question = form.save()
        question = bank.questions.prefetch_related("options", "concept_links__concept").get(
            pk=question.pk
        )
        return Response(_standalone_question_editor_payload(bank, question))


class TeacherStandaloneQuestionImportPreviewView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_question_import_preview",
        request=FileImportRequestSerializer,
        responses={200: ManagementQuestionImportPreviewResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def post(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        require_writable_bank_content(bank)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 CSV 或 Excel 文件。"})
        sequence_floor = bank.questions.aggregate(value=Max("sequence"))["value"] or 0
        try:
            parsed = parse_question_rows(
                read_uploaded_rows(uploaded), sequence_floor=sequence_floor, allow_incomplete=True
            )
            incoming = [item.sequence for item in parsed]
            existing = list(
                bank.questions.filter(sequence__in=incoming)
                .order_by("sequence")
                .values_list("sequence", flat=True)
            )
            if existing:
                raise ValueError(
                    f"题库中已存在以下序号：{existing}。请先修改序号或编辑现有题目。"
                )
        except ValueError as error:
            raise ValidationError({"file": str(error)}) from error
        preview_id = str(uuid.uuid4())
        request.session[_standalone_question_import_session_key(bank)] = {
            "preview_id": preview_id,
            "rows": [asdict(item) for item in parsed],
        }
        request.session.modified = True
        return Response(
            {
                "preview_id": preview_id,
                "preview": {"rows": [_question_import_preview_row(item) for item in parsed]},
            }
        )


class TeacherStandaloneQuestionImportConfirmView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_question_import_confirm",
        request=ManagementQuestionImportConfirmRequestSerializer,
        responses={201: ManagementQuestionImportConfirmResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def post(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        key = _standalone_question_import_session_key(bank)
        stored = request.session.get(key, {})
        if (
            not stored
            or stored.get("preview_id") != request.data.get("preview_id")
            or not stored.get("rows")
        ):
            return _api_error_response(
                "question_import_preview_invalid",
                "导入预览已失效，请重新上传文件。",
                status.HTTP_409_CONFLICT,
            )
        parsed = [ParsedQuestion(**item) for item in stored["rows"]]
        try:
            with transaction.atomic():
                bank = lock_writable_bank_content(bank)
                imported = import_questions(bank, parsed)
        except ValueError as error:
            request.session.pop(key, None)
            request.session.modified = True
            return _api_error_response(
                "question_import_preview_stale",
                f"导入预览已失效：{error}。请重新上传文件。",
                status.HTTP_409_CONFLICT,
            )
        request.session.pop(key, None)
        request.session.modified = True
        return Response(
            {
                "created": len(imported),
                "active": sum(question.status == Question.Status.ACTIVE for question in imported),
                "draft": sum(question.status == Question.Status.DRAFT for question in imported),
                "detail_url": reverse("question_bank:bank_detail", args=[bank.pk]),
            },
            status=status.HTTP_201_CREATED,
        )


class TeacherStandaloneQuestionKCImportPreviewView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_kc_import_preview",
        request=FileImportRequestSerializer,
        responses={200: ManagementStandaloneKCImportPreviewResponseSerializer, 400: ManagementErrorResponseSerializer},
    )
    def post(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        require_writable_bank_content(bank)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 CSV 或 Excel 文件。"})
        try:
            mappings = parse_kc_rows(read_uploaded_rows(uploaded))
            missing = [
                item.sequence
                for item in mappings
                if not bank.questions.filter(sequence=item.sequence).exists()
            ]
            if missing:
                raise ValueError(f"题库中不存在题目序号：{missing}")
        except ValueError as error:
            raise ValidationError({"file": str(error)}) from error
        preview_id = str(uuid.uuid4())
        request.session[_standalone_kc_import_session_key(bank)] = {
            "preview_id": preview_id,
            "mappings": [asdict(item) for item in mappings],
        }
        request.session.modified = True
        return Response(
            {
                "preview_id": preview_id,
                "preview": {
                    "rows": [
                        {
                            "sequence": item.sequence,
                            "primary": item.primary,
                            "secondary": item.secondary,
                        }
                        for item in mappings
                    ]
                },
            }
        )


class TeacherStandaloneQuestionKCImportConfirmView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_kc_import_confirm",
        request=ManagementQuestionImportConfirmRequestSerializer,
        responses={200: ManagementQuestionsUpdatedResponseSerializer, 409: ManagementErrorResponseSerializer},
    )
    def post(self, request, bank_id):
        bank = _standalone_bank_for_user(request.user, bank_id)
        key = _standalone_kc_import_session_key(bank)
        stored = request.session.get(key, {})
        if (
            not stored
            or stored.get("preview_id") != request.data.get("preview_id")
            or not stored.get("mappings")
        ):
            return _api_error_response(
                "kc_import_preview_invalid",
                "导入预览已失效，请重新上传文件。",
                status.HTTP_409_CONFLICT,
            )
        try:
            with transaction.atomic():
                bank = lock_writable_bank_content(bank)
                updated = import_kc_mappings(
                    bank,
                    request.user,
                    [ParsedKCMapping(**item) for item in stored["mappings"]],
                )
        except ValueError as error:
            return _api_error_response(
                "standalone_kc_mapping_unsupported",
                str(error),
                status.HTTP_409_CONFLICT,
            )
        request.session.pop(key, None)
        request.session.modified = True
        log_action(request, "kc.import", bank, {"count": updated})
        return Response({"questions_updated": updated})


class TeacherStandaloneSharedQuestionBankCopyView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_shared_copy",
        request=None,
        responses=ManagementStandaloneBankSerializer,
    )
    def post(self, request, source_bank_id):
        source = get_object_or_404(
            QuestionBank.objects.filter(visibility=QuestionBank.Visibility.SHARED),
            pk=source_bank_id,
        )
        with transaction.atomic():
            bank = copy_shared_bank(source, request.user)
            log_action(request, "question_bank.copy", bank, {"source_bank_id": source.pk})
        return Response(
            _standalone_bank_payload(bank), status=status.HTTP_201_CREATED
        )


class TeacherStandaloneConceptCollectionView(TeacherQuestionBankManagementApiView):
    @extend_schema(
        operation_id="teacher_standalone_question_bank_concept_list",
        request=None,
        responses=ManagementStandaloneConceptCollectionSerializer,
    )
    def get(self, request):
        if accessible_courses(request.user).exists():
            return Response({"redirect_url": reverse("courses:list")})
        if request.user.is_superuser or request.user.role == User.Role.ADMIN:
            concepts = Concept.objects.all()
        else:
            concepts = (
                Concept.objects.filter(owner=request.user)
                | Concept.objects.filter(scope=Concept.Scope.PUBLIC)
            )
        concepts = concepts.distinct().select_related("parent").order_by("name", "pk")
        return Response(
            {
                "items": [
                    {
                        "id": concept.pk,
                        "code": concept.code,
                        "name": concept.name,
                        "description": concept.description,
                        "scope": concept.scope,
                        "scope_label": concept.get_scope_display(),
                        "status": concept.status,
                        "status_label": concept.get_status_display(),
                        "path": concept.full_path,
                    }
                    for concept in concepts
                ]
            }
        )
