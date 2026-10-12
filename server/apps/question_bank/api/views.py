from collections import defaultdict

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from django.db.models import Count, IntegerField, Prefetch, Q
from django.db.models.functions import Cast, Substr
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework import status
from apps.question_bank.services.question_readiness import readiness_issues
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Course
from apps.accounts.course_access import lock_writable_course
from apps.audit.services import log_action
from apps.api.pagination import FixedPageNumberPagination
from apps.question_bank.bank_metadata import can_edit_bank_metadata
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.question_kc_mapping import project_question_kc_mapping
from apps.question_bank.services.course_kc_import import (
    CourseKCBulkImportError,
    apply_course_kc_import,
    preview_course_kcs,
)
from apps.question_bank.services.course_kc import CourseKCService
from apps.question_bank.services.file_reader import read_uploaded_rows
from apps.question_bank.services.import_parsing import (
    parse_course_kc_rows,
    parse_question_kc_tree_rows,
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

from .serializers import (
    BankConceptCollectionSerializer,
    CourseConceptCreateRequestSerializer,
    CourseConceptCreateResponseSerializer,
    CourseConceptCollectionSerializer,
    CourseKCImportResultSerializer,
    FileImportRequestSerializer,
    ImportConfirmRequestSerializer,
    ImportPreviewResponseSerializer,
    QuestionBankCreateRequestSerializer,
    QuestionBankCreateResponseSerializer,
    QuestionBankCollectionSerializer,
    QuestionBankDetailSerializer,
    QuestionCollectionSerializer,
    QuestionKCImportResultSerializer,
)


def _course_for_teacher(user, course_id):
    course = get_object_or_404(Course, pk=course_id)
    if not course.can_manage(user):
        raise NotFound()
    return course


def _bank_for_course(course, bank_id):
    return get_object_or_404(
        course.question_banks.select_related("created_by", "owner"),
        pk=bank_id,
    )


def _course_payload(course):
    return {
        "id": course.pk,
        "name": course.name,
        "code": course.code,
        "term": course.term,
        "status": course.status,
    }


def _bank_detail_url(course_id, bank_id):
    return reverse("courses:bank_detail", args=[course_id, bank_id])


def _creator_name(bank):
    creator = bank.created_by or bank.owner or bank.course.owner
    return creator.shown_name


def _metadata_capabilities(user, course, bank):
    editable = course.status == Course.Status.ACTIVE and can_edit_bank_metadata(
        user, course, bank
    )
    return {
        "can_edit_metadata": editable,
        "visibility_update_url": reverse(
            "teacher-question-bank-editor", args=[course.pk, bank.pk]
        )
        if editable and bank.visibility != QuestionBank.Visibility.ARCHIVED
        else None,
        "archive_url": reverse("teacher-question-bank-archive", args=[course.pk, bank.pk])
        if editable and bank.visibility != QuestionBank.Visibility.ARCHIVED
        else None,
    }


def _serialize_page(paginator, items, extra):
    response = paginator.get_paginated_response(items)
    response.data.update(extra)
    return response


def _session_key(request):
    if request.session.session_key is None:
        request.session.create()
    return request.session.session_key


def _batch_conflict_response(error):
    external_code = {
        "preview_consumed": "preview_batch_consumed",
        "preview_expired": "preview_batch_invalid",
        "preview_not_found": "preview_batch_invalid",
        "preview_binding_mismatch": "preview_batch_invalid",
    }.get(error.code, error.code)
    return Response(
        {"code": external_code, "detail": str(error)},
        status=status.HTTP_409_CONFLICT,
    )


class TeacherCourseKCImportPreviewView(APIView):
    @extend_schema(
        request=FileImportRequestSerializer,
        responses=ImportPreviewResponseSerializer,
    )
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 CSV 或 Excel 文件。"})
        try:
            raw = uploaded.read()
            uploaded.seek(0)
            parsed = parse_course_kc_rows(read_uploaded_rows(uploaded))
            preview = preview_course_kcs(course=course, parsed_rows=parsed)
        except ValueError as error:
            raise ValidationError({"file": str(error)}) from error
        batch = ImportPreviewBatchService.create(
            kind="course_kc",
            course=course,
            bank=None,
            actor=request.user,
            session_key=_session_key(request),
            file_bytes=raw,
            payload=preview,
            summary={
                "rows": len(preview["rows"]),
                "conflicts": sum(row["classification"] == "conflict" for row in preview["rows"]),
            },
        )
        return Response({"batch_id": str(batch.pk), "preview": preview})


class TeacherCourseKCImportConfirmView(APIView):
    @extend_schema(
        request=ImportConfirmRequestSerializer,
        responses=CourseKCImportResultSerializer,
    )
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        try:
            batch, result = ImportPreviewBatchService.confirm(
                batch_id=request.data.get("batch_id"),
                kind="course_kc",
                course=course,
                bank=None,
                actor=request.user,
                session_key=_session_key(request),
                apply=lambda payload: apply_course_kc_import(course=course, payload=payload),
            )
        except ImportPreviewBatchConflict as error:
            return _batch_conflict_response(error)
        except CourseKCBulkImportError as error:
            return Response(
                {"code": error.code, "detail": str(error)},
                status=status.HTTP_409_CONFLICT,
            )
        log_action(
            request,
            "course_kc.import",
            course,
            {"batch_id": str(batch.pk), "file_sha256": batch.file_sha256, **result},
        )
        return Response(result)


class TeacherQuestionKCImportPreviewView(APIView):
    @extend_schema(
        request=FileImportRequestSerializer,
        responses=ImportPreviewResponseSerializer,
    )
    def post(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        uploaded = request.FILES.get("file")
        if uploaded is None:
            raise ValidationError({"file": "请选择 CSV 或 Excel 文件。"})
        try:
            raw = uploaded.read()
            uploaded.seek(0)
            parsed = parse_question_kc_tree_rows(read_uploaded_rows(uploaded))
            preview = preview_question_kc_trees(bank=bank, parsed_rows=parsed)
        except (ValueError, QuestionKCTreeImportError) as error:
            code = getattr(error, "code", "invalid_question_kc_file")
            return Response(
                {"code": code, "detail": str(error)},
                status=status.HTTP_400_BAD_REQUEST,
            )
        batch = ImportPreviewBatchService.create(
            kind="question_kc",
            course=course,
            bank=bank,
            actor=request.user,
            session_key=_session_key(request),
            file_bytes=raw,
            payload=preview,
            summary={"questions": len(preview["trees"])},
        )
        return Response({"batch_id": str(batch.pk), "preview": preview})


class TeacherQuestionKCImportConfirmView(APIView):
    @extend_schema(
        request=ImportConfirmRequestSerializer,
        responses=QuestionKCImportResultSerializer,
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
                apply=lambda payload: apply_question_kc_tree_import(bank=bank, payload=payload),
            )
        except ImportPreviewBatchConflict as error:
            return _batch_conflict_response(error)
        except QuestionKCTreeImportError as error:
            return Response(
                {"code": error.code, "detail": str(error)},
                status=status.HTTP_409_CONFLICT,
            )
        log_action(
            request,
            "question_kc.import",
            bank,
            {"batch_id": str(batch.pk), "file_sha256": batch.file_sha256, **result},
        )
        return Response(result)


class TeacherQuestionBankCollectionView(APIView):
    @extend_schema(
        operation_id="teacher_question_banks_list",
        parameters=[OpenApiParameter("q", str, required=False)],
        responses=QuestionBankCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        query = request.query_params.get("q", "").strip()
        banks = (
            course.question_banks.exclude(
                visibility=QuestionBank.Visibility.ARCHIVED
            )
            .select_related("created_by", "owner", "course__owner")
            .annotate(question_count=Count("questions"))
            .order_by("-updated_at", "pk")
        )
        if query:
            banks = banks.filter(
                Q(name__icontains=query)
                | Q(description__icontains=query)
                | Q(created_by__display_name__icontains=query)
                | Q(created_by__username__icontains=query)
            )

        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(banks, request, view=self)
        items = []
        for bank in page:
            items.append(
                {
                    "id": bank.pk,
                    "name": bank.name,
                    "description": bank.description,
                    "visibility": bank.visibility,
                    "visibility_label": bank.get_visibility_display(),
                    "question_count": bank.question_count,
                    "created_by_name": _creator_name(bank),
                    "updated_at": bank.updated_at,
                    "detail_url": _bank_detail_url(course.pk, bank.pk),
                    **_metadata_capabilities(request.user, course, bank),
                }
            )
        return _serialize_page(
            paginator,
            items,
            {
                "course": _course_payload(course),
                "summary": {
                    "archived_count": course.question_banks.filter(
                        visibility=QuestionBank.Visibility.ARCHIVED
                    ).count(),
                    "archived_banks_url": reverse(
                        "courses:archived_banks", args=[course.pk]
                    ),
                    "shared_sources_url": reverse(
                        "courses:shared_sources", args=[course.pk]
                    ),
                },
            },
        )

    @extend_schema(
        request=QuestionBankCreateRequestSerializer,
        responses={201: QuestionBankCreateResponseSerializer},
    )
    @transaction.atomic
    def post(self, request, course_id):
        course = lock_writable_course(_course_for_teacher(request.user, course_id))
        name = " ".join(str(request.data.get("name") or "").strip().split())
        description = str(request.data.get("description") or "").strip()
        visibility = request.data.get("visibility") or QuestionBank.Visibility.PRIVATE
        errors = {}
        if not name:
            errors["name"] = ["该字段是必填项。"]
        if visibility not in {QuestionBank.Visibility.PRIVATE, QuestionBank.Visibility.SHARED}:
            errors["visibility"] = ["可见性无效。"]
        if errors:
            raise ValidationError(errors)
        bank = QuestionBank.objects.create(
            course=course,
            created_by=request.user,
            name=name,
            description=description,
            visibility=visibility,
        )
        return Response(
            {
                "id": bank.pk,
                "name": bank.name,
                "detail_url": _bank_detail_url(course.pk, bank.pk),
            },
            status=status.HTTP_201_CREATED,
        )


class TeacherCourseConceptCollectionView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("all", bool, required=False),
        ],
        responses=CourseConceptCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        query = request.query_params.get("q", "").strip()
        concepts = course.concepts.annotate(
            question_count=Count("question_links", distinct=True),
            bank_count=Count("question_links__question__bank", distinct=True),
            level_1_count=Count(
                "question_links",
                filter=Q(question_links__parent_link__isnull=True),
            ),
            level_2_count=Count(
                "question_links",
                filter=Q(
                    question_links__parent_link__isnull=False,
                    question_links__parent_link__parent_link__isnull=True,
                ),
            ),
            level_3_count=Count(
                "question_links",
                filter=Q(question_links__parent_link__parent_link__isnull=False),
            ),
            kc_sequence=Cast(Substr("code", 3), IntegerField()),
        ).order_by("kc_sequence", "pk")
        if query:
            concepts = concepts.filter(name__icontains=query)

        paginator = FixedPageNumberPagination()
        if request.query_params.get("all") == "1":
            paginator.page_size = max(1, concepts.count())
        page = list(paginator.paginate_queryset(concepts, request, view=self))
        concept_ids = [concept.pk for concept in page]
        distribution = defaultdict(list)
        rows = (
            QuestionConcept.objects.filter(
                concept_id__in=concept_ids,
                question__bank__course=course,
            )
            .values("concept_id", "question__bank_id", "question__bank__name")
            .annotate(question_count=Count("question_id"))
            .order_by("question__bank__name", "question__bank_id")
        )
        for row in rows:
            bank_id = row["question__bank_id"]
            distribution[row["concept_id"]].append(
                {
                    "bank_id": bank_id,
                    "bank_name": row["question__bank__name"],
                    "question_count": row["question_count"],
                    "detail_url": _bank_detail_url(course.pk, bank_id),
                }
            )
        writable = course.status == Course.Status.ACTIVE
        items = [
            {
                "id": concept.pk,
                "kc_code": concept.code,
                "name": concept.name,
                "description": concept.description,
                "status": concept.status,
                "role_counts": {
                    "level_1": concept.level_1_count,
                    "level_2": concept.level_2_count,
                    "level_3": concept.level_3_count,
                },
                "question_count": concept.question_count,
                "bank_count": concept.bank_count,
                "distribution": distribution[concept.pk],
                "can_rename": writable,
                "can_archive": writable and concept.status == Concept.Status.ACTIVE,
                "can_restore": writable and concept.status == Concept.Status.ARCHIVED,
                "can_delete": (
                    writable
                ),
                "rename_url": reverse(
                    "teacher-course-concept-editor", args=[course.pk, concept.pk]
                )
                if writable
                else None,
                "delete_url": reverse(
                    "teacher-course-concept-editor", args=[course.pk, concept.pk]
                )
                if writable
                else None,
            }
            for concept in page
        ]
        summary = {
            "total": course.concepts.count(),
            "active": course.concepts.filter(status=Concept.Status.ACTIVE).count(),
            "used": course.concepts.filter(question_links__isnull=False).distinct().count(),
            "archived": course.concepts.filter(status=Concept.Status.ARCHIVED).count(),
        }
        return _serialize_page(
            paginator,
            items,
            {"course": _course_payload(course), "summary": summary},
        )

    @extend_schema(
        request=CourseConceptCreateRequestSerializer,
        responses={201: CourseConceptCreateResponseSerializer},
    )
    @transaction.atomic
    def post(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        try:
            concept = CourseKCService.create(
                course=lock_writable_course(course),
                sequence=request.data.get("sequence"),
                name=request.data.get("name"),
                description=str(request.data.get("description") or ""),
            )
        except DjangoValidationError as error:
            raise ValidationError({"concept": error.messages}) from error
        except IntegrityError as error:
            raise ValidationError({"name": ["当前课程已存在相同名称或编号的 KC。"]}) from error
        log_action(request, "concept.create", concept, {
            "course_id": course.pk, "kc_code": concept.code, "name": concept.name,
        })
        return Response(
            {
                "id": concept.pk,
                "kc_code": concept.code,
                "name": concept.name,
                "description": concept.description,
                "status": concept.status,
            },
            status=status.HTTP_201_CREATED,
        )


class TeacherQuestionBankDetailView(APIView):
    @extend_schema(
        operation_id="teacher_question_banks_retrieve",
        responses=QuestionBankDetailSerializer,
    )
    def get(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        bank.question_count = bank.questions.count()
        capabilities = _metadata_capabilities(request.user, course, bank)
        can_edit_content = (
            course.status == Course.Status.ACTIVE
            and bank.visibility != QuestionBank.Visibility.ARCHIVED
        )
        payload = {
            "id": bank.pk,
            "name": bank.name,
            "description": bank.description,
            "visibility": bank.visibility,
            "visibility_label": bank.get_visibility_display(),
            "question_count": bank.question_count,
            "created_by_name": _creator_name(bank),
            "updated_at": bank.updated_at,
            "can_edit_metadata": capabilities["can_edit_metadata"],
            "can_edit_content": can_edit_content,
            "list_url": reverse("courses:banks", args=[course.pk]),
            "edit_url": reverse("courses:bank_edit", args=[course.pk, bank.pk])
            if capabilities["can_edit_metadata"]
            else None,
            "visibility_update_url": capabilities["visibility_update_url"],
            "archive_url": capabilities["archive_url"],
            "restore_url": reverse(
                "teacher-question-bank-restore", args=[course.pk, bank.pk]
            )
            if capabilities["can_edit_metadata"]
            and bank.visibility == QuestionBank.Visibility.ARCHIVED
            else None,
            "delete_url": reverse("teacher-question-bank-delete", args=[course.pk, bank.pk])
            if capabilities["can_edit_metadata"]
            else None,
            "question_import_url": reverse(
                "courses:question_import", args=[course.pk, bank.pk]
            )
            if can_edit_content
            else None,
            "kc_import_url": reverse(
                "courses:kc_import", args=[course.pk, bank.pk]
            )
            if can_edit_content
            else None,
            "course": _course_payload(course),
        }
        return Response(QuestionBankDetailSerializer(payload).data)


class TeacherQuestionCollectionView(APIView):
    @extend_schema(
        parameters=[
            OpenApiParameter("q", str, required=False),
            OpenApiParameter("question_type", str, required=False),
            OpenApiParameter("status", str, required=False),
        ],
        responses=QuestionCollectionSerializer,
    )
    def get(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        query = request.query_params.get("q", "").strip()
        question_type = request.query_params.get("question_type", "").strip()
        status = request.query_params.get("status", "").strip()
        type_values = {value for value, _label in Question.Type.choices}
        status_values = {value for value, _label in Question.Status.choices}
        if question_type and question_type not in type_values:
            raise ValidationError({"question_type": "未知题型。"})
        if status and status not in status_values:
            raise ValidationError({"status": "未知题目状态。"})

        questions = (
            bank.questions.select_related("course_identity")
            .prefetch_related(
                Prefetch(
                    "concept_links",
                    queryset=QuestionConcept.objects.select_related("concept").order_by(
                        "display_order", "concept__code", "concept_id"
                    ),
                )
            )
            .order_by("sequence", "pk")
        )
        if query:
            questions = questions.filter(
                Q(text__icontains=query)
                | Q(code__icontains=query)
                | Q(course_identity__stable_code__icontains=query)
                | Q(concept_links__concept__name__icontains=query)
            ).distinct()
        if question_type:
            questions = questions.filter(question_type=question_type)
        if status:
            questions = questions.filter(status=status)

        paginator = FixedPageNumberPagination()
        page = paginator.paginate_queryset(questions, request, view=self)
        writable = (
            course.status == Course.Status.ACTIVE
            and bank.visibility != QuestionBank.Visibility.ARCHIVED
        )
        items = []
        for question in page:
            identity = getattr(question, "course_identity", None)
            items.append(
                {
                    "id": question.pk,
                    "course_sequence": identity.course_sequence if identity else None,
                    "stable_code": identity.stable_code if identity else "",
                    "sequence": question.sequence,
                    "code": question.code,
                    "text": question.text,
                    "question_type": question.question_type,
                    "question_type_label": question.get_question_type_display(),
                    "kc_mapping": project_question_kc_mapping(question),
                    "teacher_difficulty": (
                        format(question.teacher_difficulty, "f")
                        if question.teacher_difficulty is not None
                        else None
                    ),
                    "status": question.status,
                    "status_label": question.get_status_display(),
                    "draft_reasons": readiness_issues(question) if question.status == Question.Status.DRAFT else [],
                    "edit_url": reverse(
                        "courses:question_edit",
                        args=[course.pk, bank.pk, question.pk],
                    )
                    if writable
                    else None,
                    "delete_url": reverse(
                        "teacher-question-delete",
                        args=[course.pk, bank.pk, question.pk],
                    )
                    if writable
                    else None,
                }
            )
        return _serialize_page(
            paginator,
            items,
            {
                "bank": {"id": bank.pk, "name": bank.name},
                "filters": {
                    "query": query,
                    "question_type": question_type,
                    "status": status,
                    "question_types": [
                        {"value": value, "label": label}
                        for value, label in Question.Type.choices
                    ],
                    "statuses": [
                        {"value": value, "label": label}
                        for value, label in Question.Status.choices
                    ],
                },
            },
        )


class TeacherBankConceptCollectionView(APIView):
    @extend_schema(
        parameters=[OpenApiParameter("q", str, required=False)],
        responses=BankConceptCollectionSerializer,
    )
    def get(self, request, course_id, bank_id):
        course = _course_for_teacher(request.user, course_id)
        bank = _bank_for_course(course, bank_id)
        query = request.query_params.get("q", "").strip()
        concepts = (
            Concept.objects.filter(question_links__question__bank=bank)
            .annotate(
                primary_count=Count(
                    "question_links",
                    filter=Q(
                        question_links__question__bank=bank,
                        question_links__is_primary=True,
                    ),
                ),
                secondary_count=Count(
                    "question_links",
                    filter=Q(
                        question_links__question__bank=bank,
                        question_links__is_primary=False,
                    ),
                ),
                level_1_count=Count(
                    "question_links",
                    filter=Q(
                        question_links__question__bank=bank,
                        question_links__parent_link__isnull=True,
                    ),
                ),
                level_2_count=Count(
                    "question_links",
                    filter=Q(
                        question_links__question__bank=bank,
                        question_links__parent_link__isnull=False,
                        question_links__parent_link__parent_link__isnull=True,
                    ),
                ),
                level_3_count=Count(
                    "question_links",
                    filter=Q(
                        question_links__question__bank=bank,
                        question_links__parent_link__parent_link__isnull=False,
                    ),
                ),
                question_count=Count(
                    "question_links__question",
                    filter=Q(question_links__question__bank=bank),
                    distinct=True,
                ),
            )
            .order_by("name", "pk")
            .distinct()
        )
        if query:
            concepts = concepts.filter(name__icontains=query)

        paginator = FixedPageNumberPagination()
        page = list(paginator.paginate_queryset(concepts, request, view=self))
        concept_ids = [concept.pk for concept in page]
        linked_questions = defaultdict(list)
        writable = (
            course.status == Course.Status.ACTIVE
            and bank.visibility != QuestionBank.Visibility.ARCHIVED
        )
        links = (
            QuestionConcept.objects.filter(
                concept_id__in=concept_ids,
                question__bank=bank,
            )
            .select_related("question")
            .order_by("concept_id", "question__sequence", "question_id")
        )
        for link in links:
            question = link.question
            linked_questions[link.concept_id].append(
                {
                    "id": question.pk,
                    "sequence": question.sequence,
                    "code": question.code,
                    "edit_url": reverse(
                        "courses:question_edit",
                        args=[course.pk, bank.pk, question.pk],
                    )
                    if writable
                    else None,
                }
            )
        items = [
            {
                "id": concept.pk,
                "kc_code": concept.code,
                "name": concept.name,
                "level_1_count": concept.level_1_count,
                "level_2_count": concept.level_2_count,
                "level_3_count": concept.level_3_count,
                "question_count": concept.question_count,
                "questions": linked_questions[concept.pk],
            }
            for concept in page
        ]
        return _serialize_page(
            paginator,
            items,
            {"bank": {"id": bank.pk, "name": bank.name}},
        )
