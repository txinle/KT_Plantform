from dataclasses import asdict
from django.contrib import messages
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.http import HttpResponseBadRequest, JsonResponse
from django.shortcuts import get_object_or_404, redirect
from django.views.decorators.http import require_GET, require_POST

from apps.accounts.course_access import (
    get_accessible_course,
    lock_writable_course,
    require_writable_course,
)
from apps.accounts.models import Course, User
from apps.accounts.permissions import role_required
from apps.api.frontend_entry import frontend_response
from apps.audit.services import log_action

from .access import lock_writable_course_bank, require_writable_bank_content
from .audit import bank_metadata_audit_details
from .forms import CourseConceptForm, QuestionBankForm, QuestionBankVisibilityForm, QuestionEditForm, UploadForm
from .models import Concept, ImportPreviewBatch, QuestionBank
from .services.file_reader import read_uploaded_rows, read_uploaded_workbook
from .services.import_parsing import (
    ParsedKCMapping,
    ParsedKCNode,
    ParsedKCWorkbook,
    ParsedQuestionKCRelation,
    ParsedQuestionKCRoot,
    parse_kc_rows,
    parse_kc_workbook,
    parse_question_kc_tree_rows,
)
from .services.import_service import (
    copy_shared_bank,
    import_kc_mappings,
    import_kc_workbook,
)
from .services.question_kc_import import (
    apply_question_kc_tree_import,
    preview_question_kc_trees,
)
from .services.lifecycle import (
    ReferencedQuestionBankError,
    ReferencedQuestionError,
    delete_question,
    delete_question_bank,
)
from .services.preview_batch import ImportPreviewBatchConflict
from .services.unified_import import UnifiedQuestionImportService, _project_row


def _bank(course, pk):
    return get_object_or_404(course.question_banks, pk=pk)


def can_edit_bank_metadata(user, course, bank):
    return bool(
        user.is_superuser
        or user.role == User.Role.ADMIN
        or course.can_manage(user)
        or bank.owner_id == user.id
    )


def _require_bank_metadata_editor(request, course, bank):
    if can_edit_bank_metadata(request.user, course, bank):
        return bank
    raise PermissionDenied("只有课程共同管理者或题库所有者可以修改题库元数据。")


ACTIVE_BANK_VISIBILITY_CHOICES = [
    (QuestionBank.Visibility.PRIVATE, QuestionBank.Visibility.PRIVATE.label),
    (QuestionBank.Visibility.SHARED, QuestionBank.Visibility.SHARED.label),
]


def _active_question_bank_form(data=None):
    form = QuestionBankForm(data)
    form.fields["visibility"].choices = ACTIVE_BANK_VISIBILITY_CHOICES
    return form


def _metadata_question_bank_form(data=None, *, instance):
    form = QuestionBankForm(data, instance=instance)
    if instance.visibility == QuestionBank.Visibility.ARCHIVED:
        form.fields["visibility"].choices = [
            (QuestionBank.Visibility.ARCHIVED, QuestionBank.Visibility.ARCHIVED.label),
            (QuestionBank.Visibility.PRIVATE, QuestionBank.Visibility.PRIVATE.label),
        ]
    else:
        form.fields["visibility"].choices = ACTIVE_BANK_VISIBILITY_CHOICES
    return form


def _frontend_entry_for_safe_method(request):
    if request.method in {"GET", "HEAD"}:
        return frontend_response()
    return None


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_list(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if request.method == "POST":
        require_writable_course(course)
        form = _active_question_bank_form(request.POST)
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
        return frontend_response()
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def archived_bank_list(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    return frontend_response()


def _shared_banks_for(course):
    return QuestionBank.objects.filter(
        visibility=QuestionBank.Visibility.SHARED,
        course__isnull=False,
    ).exclude(course=course)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def shared_source_courses(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def shared_creators(request, course_pk, source_course_pk):
    course = get_accessible_course(request.user, course_pk)
    source_course = get_object_or_404(
        Course.objects.filter(question_banks__in=_shared_banks_for(course)).distinct(),
        pk=source_course_pk,
    )
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def shared_creator_banks(request, course_pk, source_course_pk, creator_pk):
    course = get_accessible_course(request.user, course_pk)
    source_course = get_object_or_404(
        Course.objects.filter(question_banks__in=_shared_banks_for(course)).distinct(),
        pk=source_course_pk,
    )
    creator = get_object_or_404(
        User.objects.filter(
            created_question_banks__course=source_course,
            created_question_banks__visibility=QuestionBank.Visibility.SHARED,
        ).distinct(),
        pk=creator_pk,
    )
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_detail(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
@require_GET
def bank_edit(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    _require_bank_metadata_editor(request, course, bank)
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    return frontend_response()


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_update(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    with transaction.atomic():
        course, bank = lock_writable_course_bank(course, bank)
        _require_bank_metadata_editor(request, course, bank)
        form = _metadata_question_bank_form(request.POST, instance=bank)
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
    return frontend_response()


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
@transaction.atomic
def concept_create(request, course_pk):
    course = get_accessible_course(request.user, course_pk)
    course = lock_writable_course(course)
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
        messages.error(
            request,
            "课程 KC 创建失败："
            + "; ".join(error for errors in form.errors.values() for error in errors),
        )
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
@transaction.atomic
def concept_rename(request, course_pk, concept_pk):
    course = get_accessible_course(request.user, course_pk)
    course = lock_writable_course(course)
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
        messages.error(
            request,
            "课程 KC 重命名失败："
            + "; ".join(error for errors in form.errors.values() for error in errors),
        )
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
@transaction.atomic
def concept_delete(request, course_pk, concept_pk):
    course = get_accessible_course(request.user, course_pk)
    course = lock_writable_course(course)
    concept = get_object_or_404(course.concepts, pk=concept_pk)
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
            {"course_id": course.pk, "name": concept.name, "kc_code": concept.code},
        )
        messages.success(request, "已被引用的课程 KC 已归档，历史映射保持不变。")
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
        form.fields["visibility"].choices = ACTIVE_BANK_VISIBILITY_CHOICES
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


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_archive(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    with transaction.atomic():
        course, bank = lock_writable_course_bank(course, bank)
        _require_bank_metadata_editor(request, course, bank)
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
    messages.success(request, "题库已归档，可在归档题库页面查看或恢复。")
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_restore(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    with transaction.atomic():
        course, bank = lock_writable_course_bank(course, bank)
        _require_bank_metadata_editor(request, course, bank)
        if bank.visibility == QuestionBank.Visibility.ARCHIVED:
            bank.visibility = QuestionBank.Visibility.PRIVATE
            bank.save(update_fields=["visibility", "updated_at"])
            log_action(
                request,
                "question_bank.restore",
                bank,
                {"bank_id": bank.pk, "course_id": course.pk},
            )
    messages.success(request, "题库已恢复为私有题库。")
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def bank_delete(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再永久删除题库。")
    try:
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank)
            _require_bank_metadata_editor(request, course, bank)
            details = delete_question_bank(bank)
            log_action(request, "question_bank.delete", details=details)
    except ReferencedQuestionBankError:
        messages.error(
            request,
            "题库已被实验或发布快照引用，不能永久删除。请保留并归档，或先处理无研究数据的草稿实验。",
        )
        return redirect("courses:bank_detail", course.pk, bank.pk)
    messages.success(request, "题库及其未被研究数据引用的题目已永久删除。")
    return redirect("courses:banks", course.pk)


@require_POST
@role_required(User.Role.TEACHER, User.Role.ADMIN)
def question_delete(request, course_pk, bank_pk, question_pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, bank_pk)
    if request.POST.get("confirmation") != "delete":
        return HttpResponseBadRequest("请在二次确认后再永久删除题目。")
    try:
        with transaction.atomic():
            course, bank = lock_writable_course_bank(course, bank, content=True)
            question = get_object_or_404(
                bank.questions.select_for_update().select_related("bank"),
                pk=question_pk,
            )
            details = delete_question(question)
            log_action(request, "question.delete", details=details)
    except ReferencedQuestionError:
        messages.error(
            request,
            "题目已被实验或发布快照引用，不能永久删除。请保留题目，或先处理无研究数据的草稿实验。",
        )
        return redirect("courses:bank_detail", course.pk, bank.pk)
    messages.success(request, "题目已永久删除，其他题目的序号和稳定课程代码保持不变。")
    return redirect("courses:bank_detail", course.pk, bank.pk)


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def question_edit(request, course_pk, bank_pk, question_pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, bank_pk)
    if request.method == "GET":
        require_writable_bank_content(bank)
        if entry := _frontend_entry_for_safe_method(request):
            return entry
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
        question = get_object_or_404(bank.questions, pk=question_pk)
        form = QuestionEditForm(instance=question, course=course)
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def question_import(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    if request.method == "POST":
        require_writable_course(course)
    require_writable_bank_content(bank)
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    if not request.session.session_key:
        request.session.save()
    preview = None
    preview_id = None
    result = None
    form = UploadForm()
    form.fields['file'].label = 'Excel、CSV 或 Word 文件'
    form.fields['file'].widget.attrs['accept'] = '.csv,.xlsx,.docx'
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            preview_id = request.POST.get('preview_id')
            try:
                result = UnifiedQuestionImportService.confirm(
                    bank=bank, actor=request.user, session_key=request.session.session_key,
                    preview_id=preview_id, audit_request=request,
                )
            except ImportPreviewBatchConflict as error:
                messages.error(request, str(error))
            else:
                batch = ImportPreviewBatch.objects.get(pk=preview_id)
                outcomes = {row['row_id']: row for row in result['rows']}
                rows = []
                for ordinal, stored in enumerate(batch.payload['rows'], 1):
                    row = _project_row(stored, ordinal, f'第 {ordinal} 条')
                    row['errors'] = outcomes[row['row_id']]['errors']
                    row['outcome'] = outcomes[row['row_id']]['status']
                    rows.append(row)
                preview = {'rows': rows, 'summary': batch.summary, 'confirmable': False}
        else:
            form = UploadForm(request.POST, request.FILES)
            form.fields['file'].label = 'Excel、CSV 或 Word 文件'
            form.fields['file'].widget.attrs['accept'] = '.csv,.xlsx,.docx'
            if form.is_valid():
                try:
                    data = UnifiedQuestionImportService.preview(
                        bank=bank, actor=request.user, session_key=request.session.session_key,
                        uploaded=form.cleaned_data['file'],
                    )
                except ValueError as exc:
                    form.add_error("file", str(exc))
                else:
                    preview, preview_id = data['preview'], data['preview_id']
    return frontend_response()


@role_required(User.Role.TEACHER, User.Role.ADMIN)
def kc_import(request, course_pk, pk):
    course = get_accessible_course(request.user, course_pk)
    bank = _bank(course, pk)
    if request.method == "POST":
        require_writable_course(course)
    require_writable_bank_content(bank)
    if entry := _frontend_entry_for_safe_method(request):
        return entry
    session_key = f"course_kc_import:{course.pk}:{bank.pk}"
    preview = None
    preview_v2 = None
    preview_tree = None
    upload_name = ""
    form = UploadForm()
    if request.method == "POST":
        if request.POST.get("action") == "confirm":
            stored = request.session.get(session_key, {})
            tree_payload = None
            if stored.get("version") == "tree_v2":
                tree_payload = stored.get("payload", {})
                workbook = None
                mappings = None
            elif stored.get("version") == "legacy_workbook":
                payload = stored.get("payload", {})
                workbook = ParsedKCWorkbook(
                    nodes=[ParsedKCNode(**item) for item in payload.get("nodes", [])],
                    roots=[ParsedQuestionKCRoot(**item) for item in payload.get("roots", [])],
                    relations=[ParsedQuestionKCRelation(**item) for item in payload.get("relations", [])],
                )
                mappings = None
            else:
                workbook = None
                mappings = [ParsedKCMapping(**item) for item in stored.get("payload", [])]
                tree_payload = None
            if workbook is None and not mappings and not tree_payload:
                messages.error(request, "导入预览已失效，请重新上传文件。")
            else:
                try:
                    with transaction.atomic():
                        course, bank = lock_writable_course_bank(course, bank, content=True)
                        if tree_payload is not None:
                            result = apply_question_kc_tree_import(bank=bank, payload=tree_payload)
                            count = result["questions_updated"]
                        elif workbook is not None:
                            result = import_kc_workbook(bank, workbook)
                            count = result["questions_updated"]
                        else:
                            count = len(mappings)
                            import_kc_mappings(bank, request.user, mappings)
                        log_action(request, "kc.import", bank, {"course_id": course.pk, "count": count})
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    request.session.pop(session_key, None)
                    messages.success(request, f"已更新 {count} 道题的 KC 映射。")
                    return redirect("courses:bank_detail", course.pk, bank.pk)
        else:
            request.session.pop(session_key, None)
            form = UploadForm(request.POST, request.FILES)
            if form.is_valid():
                upload_name = form.cleaned_data["file"].name
                try:
                    uploaded = form.cleaned_data["file"]
                    if uploaded.name.lower().endswith(".xlsx"):
                        sheets = read_uploaded_workbook(uploaded)
                        if {"KC节点", "题目映射", "题目KC关系"}.issubset(sheets):
                            parsed_v2 = parse_kc_workbook(sheets)
                            stable_codes = {item.stable_question_code for item in parsed_v2.roots}
                            existing_codes = set(
                                bank.questions.filter(
                                    course_identity__stable_code__in=stable_codes
                                ).values_list("course_identity__stable_code", flat=True)
                            )
                            missing_codes = sorted(stable_codes - existing_codes)
                            if missing_codes:
                                raise ValueError(f"题库中不存在稳定题号：{missing_codes}")
                            request.session[session_key] = {
                                "version": "legacy_workbook",
                                "payload": asdict(parsed_v2),
                            }
                            preview_v2 = parsed_v2
                            parsed = None
                        else:
                            uploaded.seek(0)
                            rows = read_uploaded_rows(uploaded)
                            headers = tuple(str(key).strip() for key in (rows[0].keys() if rows else ()))
                            if headers == ("题目序号", "一级知识点", "二级知识点", "三级知识点"):
                                parsed_tree = parse_question_kc_tree_rows(rows)
                                preview_tree = preview_question_kc_trees(bank=bank, parsed_rows=parsed_tree)
                                request.session[session_key] = {"version": "tree_v2", "payload": preview_tree}
                                parsed = None
                            else:
                                parsed = parse_kc_rows(rows)
                    else:
                        rows = read_uploaded_rows(uploaded)
                        headers = tuple(str(key).strip() for key in (rows[0].keys() if rows else ()))
                        if headers == ("题目序号", "一级知识点", "二级知识点", "三级知识点"):
                            parsed_tree = parse_question_kc_tree_rows(rows)
                            preview_tree = preview_question_kc_trees(bank=bank, parsed_rows=parsed_tree)
                            request.session[session_key] = {"version": "tree_v2", "payload": preview_tree}
                            parsed = None
                        else:
                            parsed = parse_kc_rows(rows)
                    if parsed is not None:
                        missing = [item.sequence for item in parsed if not bank.questions.filter(sequence=item.sequence).exists()]
                        if missing:
                            raise ValueError(f"题库中不存在题目序号：{missing}")
                        request.session[session_key] = {
                            "version": "legacy",
                            "payload": [asdict(item) for item in parsed],
                        }
                except ValueError as exc:
                    form.add_error("file", str(exc))
                else:
                    preview = parsed
    return frontend_response()


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
