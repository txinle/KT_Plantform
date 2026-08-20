from dataclasses import asdict
from django.conf import settings
from django.contrib import messages
from django.http import FileResponse, Http404
from django.db.models import Count
from django.db.models import Q
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import get_object_or_404, redirect, render
from apps.accounts.permissions import role_required
from apps.accounts.course_access import accessible_courses
from apps.audit.services import log_action
from .access import require_writable_bank_content
from .audit import bank_metadata_audit_details
from .forms import QuestionBankForm, QuestionBankVisibilityForm, QuestionEditForm, UploadForm
from .models import Concept, Question, QuestionBank
from .services.file_reader import read_uploaded_rows
from .services.import_parsing import ParsedKCMapping, ParsedQuestion, parse_kc_rows, parse_question_rows
from .services.import_service import copy_shared_bank, import_kc_mappings, import_questions


def _owned_bank(request, pk):
    qs = (
        QuestionBank.objects.all()
        if request.user.is_superuser or request.user.role == "admin"
        else QuestionBank.objects.filter(Q(owner=request.user) | Q(course__in=accessible_courses(request.user)))
    )
    return get_object_or_404(qs, pk=pk)


def _fixture_download(filename):
    path = settings.BASE_DIR / "fixtures" / filename
    if not path.is_file():
        raise Http404("示例文件不存在")
    return FileResponse(
        path.open("rb"),
        as_attachment=True,
        filename=filename,
        content_type="text/csv; charset=utf-8",
    )


@role_required("teacher", "admin")
@require_GET
def question_import_template(request):
    return _fixture_download("question_import_template.csv")


@role_required("teacher", "admin")
@require_GET
def kc_import_template(request):
    return _fixture_download("kc_mapping_template.csv")

@role_required("teacher", "admin")
def dashboard(request):
    if accessible_courses(request.user).exists():
        return redirect("courses:list")
    from apps.experiments.models import Experiment
    banks = QuestionBank.objects.filter(owner=request.user)
    recent_experiments = Experiment.objects.filter(owner=request.user).select_related("question_bank")[:5]
    return render(request, "teacher/dashboard.html", {
        "banks": banks,
        "question_count": Question.objects.filter(bank__owner=request.user).count(),
        "recent_experiments": recent_experiments,
    })

@role_required("teacher", "admin")
def bank_list(request):
    courses = accessible_courses(request.user).filter(status="active")
    if request.method == "GET" and courses.exists():
        return redirect("courses:list")
    banks = QuestionBank.objects.filter(owner=request.user).annotate(question_count=Count("questions"))
    shared_banks = QuestionBank.objects.filter(visibility=QuestionBank.Visibility.SHARED).exclude(owner=request.user)
    if request.method == "POST":
        form = QuestionBankForm(request.POST)
        if form.is_valid():
            bank = form.save(commit=False)
            if courses.count() == 1:
                bank.course = courses.get()
                bank.created_by = request.user
            elif courses.count() > 1:
                messages.error(request, "请先进入目标课程，再创建题库。")
                return redirect("courses:list")
            bank.owner = request.user; bank.save()
            messages.success(request, "题库已创建")
            if bank.course_id:
                return redirect("courses:bank_detail", bank.course_id, bank.pk)
            return redirect("question_bank:bank_detail", pk=bank.pk)
    else: form = QuestionBankForm()
    return render(request, "teacher/bank_list.html", {
        "banks": banks,
        "shared_banks": shared_banks,
        "form": form,
        "visibility_choices": QuestionBank.Visibility.choices,
    })

@role_required("teacher", "admin")
def bank_detail(request, pk):
    bank = _owned_bank(request, pk)
    if bank.course_id:
        return redirect("courses:bank_detail", bank.course_id, bank.pk)
    return render(request, "teacher/bank_detail.html", {"bank": bank, "questions": bank.questions.prefetch_related("options", "concept_links__concept")})


@role_required("teacher", "admin")
@require_POST
def bank_visibility_update(request, pk):
    bank = _owned_bank(request, pk)
    if bank.course_id:
        from . import course_views
        return course_views.bank_visibility_update(request, bank.course_id, bank.pk)
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
        messages.success(request, f"题库“{bank.name}”的可见性已更新")
    else:
        messages.error(request, "可见性更新失败，请重新选择")
    return redirect("question_bank:bank_list")

@role_required("teacher", "admin")
def question_edit(request, bank_pk, question_pk):
    bank = _owned_bank(request, bank_pk)
    if bank.course_id:
        from . import course_views
        return course_views.question_edit(request, bank.course_id, bank.pk, question_pk)
    require_writable_bank_content(bank)
    question = get_object_or_404(bank.questions, pk=question_pk)
    form = QuestionEditForm(request.POST or None, instance=question)
    if request.method == "POST" and form.is_valid():
        form.save(); messages.success(request, "题目已保存")
        return redirect("question_bank:bank_detail", pk=bank.pk)
    return render(request, "teacher/question_edit.html", {"bank": bank, "question": question, "form": form})

@role_required("teacher", "admin")
def question_import(request, pk):
    bank = _owned_bank(request, pk)
    if bank.course_id:
        from . import course_views
        return course_views.question_import(request, bank.course_id, bank.pk)
    require_writable_bank_content(bank)
    preview = None
    upload_name = ""
    if request.method == "POST" and request.POST.get("action") == "confirm":
        stored = request.session.pop(f"question_import_{bank.pk}", [])
        parsed = [ParsedQuestion(**item) for item in stored]
        import_questions(bank, parsed); messages.success(request, f"已导入 {len(parsed)} 道题")
        return redirect("question_bank:bank_detail", pk=bank.pk)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload_name = form.cleaned_data["file"].name
        try:
            parsed = parse_question_rows(read_uploaded_rows(form.cleaned_data["file"]))
            incoming = [item.sequence for item in parsed]
            existing = list(bank.questions.filter(sequence__in=incoming).values_list("sequence", flat=True))
            if existing:
                raise ValueError(f"题库中已存在以下序号：{sorted(existing)}。请先修改序号或编辑现有题目。")
            request.session[f"question_import_{bank.pk}"] = [asdict(item) for item in parsed]
            preview = parsed
        except ValueError as exc: form.add_error("file", str(exc))
    return render(request, "teacher/import_questions.html", {"bank": bank, "form": form, "preview": preview, "upload_name": upload_name})

@role_required("teacher", "admin")
def kc_import(request, pk):
    bank = _owned_bank(request, pk)
    if bank.course_id:
        from . import course_views
        return course_views.kc_import(request, bank.course_id, bank.pk)
    require_writable_bank_content(bank)
    preview = None
    upload_name = ""
    if request.method == "POST" and request.POST.get("action") == "confirm":
        stored = request.session.pop(f"kc_import_{bank.pk}", [])
        mappings = [ParsedKCMapping(**item) for item in stored]
        import_kc_mappings(bank, request.user, mappings)
        log_action(request, "kc.import", bank, {"count": len(mappings)})
        messages.success(request, f"已更新 {len(mappings)} 道题的 KC")
        return redirect("question_bank:bank_detail", pk=bank.pk)
    form = UploadForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        upload_name = form.cleaned_data["file"].name
        try:
            parsed = parse_kc_rows(read_uploaded_rows(form.cleaned_data["file"]))
            missing = [item.sequence for item in parsed if not bank.questions.filter(sequence=item.sequence).exists()]
            if missing: raise ValueError(f"题库中不存在题目序号：{missing}")
            request.session[f"kc_import_{bank.pk}"] = [asdict(item) for item in parsed]
            preview = parsed
        except ValueError as exc: form.add_error("file", str(exc))
    return render(request, "teacher/import_kcs.html", {"bank": bank, "form": form, "preview": preview, "upload_name": upload_name})

@role_required("teacher", "admin")
def concept_list(request):
    if accessible_courses(request.user).exists():
        return redirect("courses:list")
    if request.user.is_superuser or request.user.role == "admin":
        concepts = Concept.objects.all()
    else:
        concepts = Concept.objects.filter(owner=request.user) | Concept.objects.filter(scope=Concept.Scope.PUBLIC)
    return render(request, "teacher/concept_list.html", {"concepts": concepts.distinct()})


@role_required("teacher", "admin")
def copy_bank(request, pk):
    source = get_object_or_404(QuestionBank, pk=pk, visibility=QuestionBank.Visibility.SHARED)
    if request.method == "POST":
        targets = accessible_courses(request.user).filter(status="active")
        if targets.count() > 1:
            messages.error(request, "请先进入目标课程，再复制共享题库。")
            return redirect("courses:list")
        target = targets.first()
        bank = copy_shared_bank(source, target or request.user, request.user if target else None)
        log_action(request, "question_bank.copy", bank, {"source_bank_id": source.pk})
        messages.success(request, "共享题库已复制为你的私有副本")
        if bank.course_id:
            return redirect("courses:bank_detail", bank.course_id, bank.pk)
        return redirect("question_bank:bank_detail", pk=bank.pk)
    return redirect("question_bank:bank_list")
