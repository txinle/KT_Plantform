from dataclasses import asdict
from collections import Counter

from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Prefetch
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.course_access import (
    get_accessible_course,
    lock_writable_course,
    require_writable_course,
)
from apps.accounts.models import User
from apps.accounts.permissions import role_required
from apps.audit.services import log_action

from .access import lock_writable_course_bank, require_writable_bank_content
from .audit import bank_metadata_audit_details
from .forms import CourseConceptForm, QuestionBankForm, QuestionBankVisibilityForm, QuestionEditForm, UploadForm
from .models import Concept, QuestionBank, QuestionConcept
from .services.file_reader import read_uploaded_rows
from .services.import_parsing import ParsedKCMapping, ParsedQuestion, parse_kc_rows, parse_question_rows
from .services.import_service import copy_shared_bank, import_kc_mappings, import_questions


def _bank(course, pk):
    return get_object_or_404(course.question_banks, pk=pk)


def can_edit_bank_metadata(user, course, bank):
    return bool(
        user.is_superuser
        or user.role == User.Role.ADMIN
        or course.owner_id == user.id
        or bank.owner_id == user.id
    )


def _require_bank_metadata_editor(request, course, bank):
    if can_edit_bank_metadata(request.user, course, bank):
        return bank
    raise PermissionDenied("只有课程负责人或题库所有者可以修改题库元数据。")


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_list(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if request.method == "POST":
        require_writable_course(course)
        form = QuestionBankForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                course = lock_writable_course(course)
                bank = form.save(commit=False)
                bank.course = course
                bank.created_by = request.user
                bank.save()
                log_action(request, "question_bank.create", bank, {"course_id": course.pk})
            messages.success(request, "题库已创建。")
            return redirect("courses:bank_detail", course.pk, bank.pk)
    else:
        form = QuestionBankForm()
    banks = course.question_banks.annotate(question_count=Count("questions"))
    shared_banks = (
        QuestionBank.objects.filter(visibility=QuestionBank.Visibility.SHARED)
        .exclude(course=course)
        .select_related("course", "created_by", "owner")
        .annotate(question_count=Count("questions"))
    )
    concept_queryset = course.concepts.prefetch_related(
        Prefetch(
            "question_links",
            queryset=QuestionConcept.objects.select_related("question__bank").order_by(
                "question__bank__name", "question__sequence"
            ),
        )
    )
    course_concepts = []
    for concept in concept_queryset:
        distribution = Counter(link.question.bank for link in concept.question_links.all())
        course_concepts.append(
            {
                "concept": concept,
                "question_count": sum(distribution.values()),
                "bank_count": len(distribution),
                "distribution": sorted(distribution.items(), key=lambda item: item[0].name.casefold()),
            }
        )
    return render(
        request,
        "teacher/course_bank_list.html",
        {
            "course": course,
            "current_course": course,
            "banks": banks,
            "shared_banks": shared_banks,
            "form": form,
            "visibility_choices": QuestionBank.Visibility.choices,
            "course_concepts": course_concepts,
            "concept_form": CourseConceptForm(course=course),
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_detail(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    questions = bank.questions.prefetch_related("options", "concept_links__concept")
    chapter_map = {}
    links = QuestionConcept.objects.filter(question__bank=bank).select_related("concept", "question")
    for link in links:
        item = chapter_map.setdefault(
            link.concept_id,
            {"concept": link.concept, "primary_count": 0, "secondary_count": 0, "questions": {}},
        )
        item["primary_count" if link.is_primary else "secondary_count"] += 1
        item["questions"][link.question_id] = link.question
    chapter_concepts = sorted(chapter_map.values(), key=lambda item: item["concept"].name.casefold())
    for item in chapter_concepts:
        item["questions"] = sorted(item["questions"].values(), key=lambda question: question.sequence)
    return render(
        request,
        "teacher/course_bank_detail.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "questions": questions,
            "chapter_concepts": chapter_concepts,
            "can_edit_bank_metadata": can_edit_bank_metadata(request.user, course, bank),
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_GET
def bank_edit(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    _require_bank_metadata_editor(request, course, bank)
    form = QuestionBankForm(instance=bank)
    return render(
        request,
        "teacher/course_bank_edit.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "form": form,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_update(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    with transaction.atomic():
        course, bank = lock_writable_course_bank(course, bank)
        _require_bank_metadata_editor(request, course, bank)
        form = QuestionBankForm(request.POST, instance=bank)
        if form.is_valid():
            changed_fields = form.changed_data
            bank = form.save()
            log_action(
                request,
                "question_bank.update",
                bank,
                bank_metadata_audit_details(bank, changed_fields),
            )
            messages.success(request, "题库信息已保存。")
            return redirect("courses:bank_detail", course.pk, bank.pk)
    return render(
        request,
        "teacher/course_bank_edit.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "form": form,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def concept_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    form = CourseConceptForm(request.POST, course=course)
    if form.is_valid():
        try:
            with transaction.atomic():
                concept = form.save()
                log_action(request, "concept.create", concept, {"course_id": course.pk, "name": concept.name})
        except IntegrityError:
            messages.error(request, "课程 KC 创建失败：当前课程已存在同名 KC。")
        else:
            messages.success(request, "课程 KC 已创建。")
    else:
        messages.error(request, "课程 KC 创建失败：" + "; ".join(form.errors.get("name", [])))
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def concept_rename(request, course_pk, concept_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    concept = get_object_or_404(course.concepts, pk=concept_pk)
    old_name = concept.name
    form = CourseConceptForm(request.POST, course=course, instance=concept)
    if form.is_valid():
        try:
            with transaction.atomic():
                form.save()
                log_action(
                    request,
                    "concept.rename",
                    concept,
                    {"course_id": course.pk, "old_name": old_name, "new_name": concept.name},
                )
        except IntegrityError:
            messages.error(request, "课程 KC 重命名失败：当前课程已存在同名 KC。")
        else:
            messages.success(request, "课程 KC 已重命名。")
    else:
        messages.error(request, "课程 KC 重命名失败：" + "; ".join(form.errors.get("name", [])))
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
@transaction.atomic
def concept_delete(request, course_pk, concept_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    concept = get_object_or_404(course.concepts, pk=concept_pk)
    if concept.question_links.exists():
        messages.error(request, "该 KC 已被题目引用，不能删除。")
        return redirect("courses:banks", course.pk)
    concept_name = concept.name
    log_action(request, "concept.delete", concept, {"course_id": course.pk, "name": concept_name})
    concept.delete()
    messages.success(request, "未引用的课程 KC 已删除。")
    return redirect("courses:banks", course.pk)


@require_GET
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def concept_search(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    query = " ".join(request.GET.get("q", "").strip().casefold().split())
    concepts = course.concepts.none()
    if query:
        concepts = course.concepts.filter(normalized_name__icontains=query).order_by("name")[:12]
    return JsonResponse({"results": [{"id": item.pk, "name": item.name} for item in concepts]})


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_visibility_update(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    with transaction.atomic():
        course, bank = lock_writable_course_bank(course, bank)
        _require_bank_metadata_editor(request, course, bank)
        form = QuestionBankVisibilityForm(request.POST, instance=bank)
        if form.is_valid():
            changed_fields = form.changed_data
            form.save()
            log_action(
                request,
                "question_bank.visibility",
                bank,
                bank_metadata_audit_details(bank, changed_fields),
            )
            messages.success(request, "题库可见性已更新。")
        else:
            messages.error(request, "题库可见性更新失败。")
    return redirect("courses:banks", course.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def question_edit(request, course_pk, bank_pk, question_pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, bank_pk)
    if request.method == "POST":
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank, content=True)
            question = get_object_or_404(bank.questions, pk=question_pk)
            form = QuestionEditForm(request.POST, instance=question, course=course)
            if form.is_valid():
                form.save()
                log_action(request, "question.update", question, {"course_id": course.pk})
                messages.success(request, "题目已保存。")
                return redirect("courses:bank_detail", course.pk, bank.pk)
    else:
        require_writable_bank_content(bank)
        question = get_object_or_404(bank.questions, pk=question_pk)
        form = QuestionEditForm(instance=question, course=course)
    return render(
        request,
        "teacher/course_question_edit.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "question": question,
            "form": form,
            "concept_search_url": reverse("courses:concept_search", args=[course.pk]),
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def question_import(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    if request.method == "POST":
        require_writable_course(course)
    require_writable_bank_content(bank)
    session_key = f"course_question_import:{course.pk}:{bank.pk}"
    preview = None
    upload_name = ""
    form = UploadForm()
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            parsed = [ParsedQuestion(**item) for item in request.session.get(session_key, [])]
            if not parsed:
                messages.error(request, "导入预览已失效，请重新上传文件。")
            else:
                with transaction.atomic():
                    course, bank = lock_writable_course_bank(course, bank, content=True)
                    import_questions(bank, parsed)
                    log_action(request, "question.import", bank, {"course_id": course.pk, "count": len(parsed)})
                request.session.pop(session_key, None)
                messages.success(request, f"已导入 {len(parsed)} 道题。")
                return redirect("courses:bank_detail", course.pk, bank.pk)
        else:
            request.session.pop(session_key, None)
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                upload_name = form.cleaned_data["file"].name
                try:
                    parsed = parse_question_rows(read_uploaded_rows(form.cleaned_data["file"]))
                    incoming = [item.sequence for item in parsed]
                    existing = list(bank.questions.filter(sequence__in=incoming).values_list("sequence", flat=True))
                    if existing:
                        raise ValueError(f"题库中已存在以下序号：{sorted(existing)}。请先修改序号或编辑现有题目。")
                except ValueError as exc:
                    form.add_error("file", str(exc))
                else:
                    request.session[session_key] = [asdict(item) for item in parsed]
                    preview = parsed
    return render(
        request,
        "teacher/import_questions.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "form": form,
            "preview": preview,
            "upload_name": upload_name,
        },
    )


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def kc_import(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    if request.method == "POST":
        require_writable_course(course)
    require_writable_bank_content(bank)
    session_key = f"course_kc_import:{course.pk}:{bank.pk}"
    preview = None
    upload_name = ""
    form = UploadForm()
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            mappings = [ParsedKCMapping(**item) for item in request.session.get(session_key, [])]
            if not mappings:
                messages.error(request, "导入预览已失效，请重新上传文件。")
            else:
                with transaction.atomic():
                    course, bank = lock_writable_course_bank(course, bank, content=True)
                    import_kc_mappings(bank, request.user, mappings)
                    log_action(request, "kc.import", bank, {"course_id": course.pk, "count": len(mappings)})
                request.session.pop(session_key, None)
                messages.success(request, f"已更新 {len(mappings)} 道题的 KC 映射。")
                return redirect("courses:bank_detail", course.pk, bank.pk)
        else:
            request.session.pop(session_key, None)
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                upload_name = form.cleaned_data["file"].name
                try:
                    parsed = parse_kc_rows(read_uploaded_rows(form.cleaned_data["file"]))
                    missing = [item.sequence for item in parsed if not bank.questions.filter(sequence=item.sequence).exists()]
                    if missing:
                        raise ValueError(f"题库中不存在题目序号：{missing}")
                except ValueError as exc:
                    form.add_error("file", str(exc))
                else:
                    request.session[session_key] = [asdict(item) for item in parsed]
                    preview = parsed
    return render(
        request,
        "teacher/import_kcs.html",
        {
            "course": course,
            "current_course": course,
            "bank": bank,
            "form": form,
            "preview": preview,
            "upload_name": upload_name,
        },
    )


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def copy_bank(request, course_pk, source_pk):
    course = get_accessible_course(request.user, course_pk)
    require_writable_course(course)
    source = get_object_or_404(QuestionBank, pk=source_pk, visibility=QuestionBank.Visibility.SHARED)
    try:
        with transaction.atomic():
            course = lock_writable_course(course)
            bank = copy_shared_bank(source, course, request.user)
            log_action(
                request,
                "question_bank.copy",
                bank,
                {"source_bank_id": source.pk, "course_id": course.pk},
            )
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("courses:banks", course.pk)
    messages.success(request, "共享题库已复制为本课程的独立副本。")
    return redirect("courses:bank_detail", course.pk, bank.pk)
