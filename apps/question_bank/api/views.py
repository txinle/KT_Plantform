from collections import defaultdict

from django.db.models import Count, Prefetch, Q
from django.shortcuts import get_object_or_404
from django.urls import reverse
from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Course
from apps.api.pagination import FixedPageNumberPagination
from apps.question_bank.course_views import can_edit_bank_metadata
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .serializers import (
    BankConceptCollectionSerializer,
    CourseConceptCollectionSerializer,
    QuestionBankCollectionSerializer,
    QuestionBankDetailSerializer,
    QuestionCollectionSerializer,
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
            "courses:bank_visibility", args=[course.pk, bank.pk]
        )
        if editable and bank.visibility != QuestionBank.Visibility.ARCHIVED
        else None,
        "archive_url": reverse("courses:bank_archive", args=[course.pk, bank.pk])
        if editable and bank.visibility != QuestionBank.Visibility.ARCHIVED
        else None,
    }


def _serialize_page(paginator, items, extra):
    response = paginator.get_paginated_response(items)
    response.data.update(extra)
    return response


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


class TeacherCourseConceptCollectionView(APIView):
    @extend_schema(
        parameters=[OpenApiParameter("q", str, required=False)],
        responses=CourseConceptCollectionSerializer,
    )
    def get(self, request, course_id):
        course = _course_for_teacher(request.user, course_id)
        query = request.query_params.get("q", "").strip()
        concepts = course.concepts.annotate(
            question_count=Count("question_links", distinct=True),
            bank_count=Count("question_links__question__bank", distinct=True),
        ).order_by("name", "pk")
        if query:
            concepts = concepts.filter(name__icontains=query)

        paginator = FixedPageNumberPagination()
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
                "name": concept.name,
                "question_count": concept.question_count,
                "bank_count": concept.bank_count,
                "distribution": distribution[concept.pk],
                "can_rename": writable,
                "can_delete": writable and concept.question_count == 0,
                "rename_url": reverse(
                    "courses:concept_rename", args=[course.pk, concept.pk]
                )
                if writable
                else None,
                "delete_url": reverse(
                    "courses:concept_delete", args=[course.pk, concept.pk]
                )
                if writable and concept.question_count == 0
                else None,
            }
            for concept in page
        ]
        return _serialize_page(
            paginator,
            items,
            {"course": _course_payload(course)},
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
                "courses:bank_restore", args=[course.pk, bank.pk]
            )
            if capabilities["can_edit_metadata"]
            and bank.visibility == QuestionBank.Visibility.ARCHIVED
            else None,
            "delete_url": reverse("courses:bank_delete", args=[course.pk, bank.pk])
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
                        "concept__name", "concept_id"
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
            primary_link = next(
                (link for link in question.concept_links.all() if link.is_primary),
                None,
            )
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
                    "primary_concept": {
                        "id": primary_link.concept_id,
                        "name": primary_link.concept.name,
                    }
                    if primary_link
                    else None,
                    "teacher_difficulty": (
                        format(question.teacher_difficulty, "f")
                        if question.teacher_difficulty is not None
                        else None
                    ),
                    "status": question.status,
                    "status_label": question.get_status_display(),
                    "edit_url": reverse(
                        "courses:question_edit",
                        args=[course.pk, bank.pk, question.pk],
                    )
                    if writable
                    else None,
                    "delete_url": reverse(
                        "courses:question_delete",
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
                "name": concept.name,
                "primary_count": concept.primary_count,
                "secondary_count": concept.secondary_count,
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
