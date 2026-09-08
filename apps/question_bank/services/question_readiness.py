"""Shared technical readiness for imported, edited and published questions."""
from decimal import Decimal, InvalidOperation

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import Question
from .numeric import normalize_finite_decimal
from .scoring import normalize_true_false_answer


def _has_text(value):
    if isinstance(value, (list, tuple, dict, set)):
        return bool(value)
    return value is not None and bool(str(value).strip())


def _choices(value):
    if isinstance(value, str):
        values = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    return {str(item).strip().upper() for item in values if item is not None and str(item).strip()}


def content_issues(content, *, question_name="题目"):
    issues = []
    kind = content.get("question_type")
    answer = content.get("answer")
    option_values = content.get("options") or {}
    options = {str(key).upper() for key in option_values}
    if not _has_text(content.get("text")):
        issues.append(f"{question_name} 的题干不能为空")
    if len(options) != len(option_values):
        issues.append(f"{question_name} 的选项编号不能重复")
    for key, value in option_values.items():
        if not _has_text(value):
            issues.append(f"{question_name} 的选项 {key} 缺少内容")
    if kind == Question.Type.SINGLE:
        choices = _choices(answer)
        if len(choices) != 1 or not choices <= options:
            issues.append(f"{question_name} 的单选题答案必须对应且仅对应一个选项")
    elif kind == Question.Type.MULTIPLE:
        choices = _choices(answer)
        if len(choices) < 2 or not choices <= options:
            issues.append(f"{question_name} 的多选题答案必须对应至少两个已有选项")
    elif kind == Question.Type.NUMERIC:
        try:
            normalize_finite_decimal(answer)
        except ValueError:
            issues.append(f"{question_name} 的数值题答案必须是有效数字")
    elif kind in {Question.Type.SHORT, Question.Type.FILL_BLANK}:
        if not _has_text(answer):
            issues.append(f"{question_name} 的文本题答案不能为空")
        accepted = content.get("accepted_answers", [])
        if not isinstance(accepted, list) or any(not _has_text(item) for item in accepted):
            issues.append(f"{question_name} 的可接受答案格式无效")
    elif kind == Question.Type.TRUE_FALSE:
        if not isinstance(answer, bool) or normalize_true_false_answer(answer) is None:
            issues.append(f"{question_name} 的判断题答案必须为正确或错误")
    else:
        issues.append(f"{question_name} 的题型无效")
    if kind not in {Question.Type.SINGLE, Question.Type.MULTIPLE} and options:
        issues.append(f"{question_name} 的非选择题不能包含自定义选项")
    for field, label in (("teacher_difficulty", "教师设定难度"), ("absolute_tolerance", "绝对误差容限"), ("relative_tolerance", "相对误差容限")):
        value = content.get(field)
        if value is None or value == "":
            continue
        try:
            number = Decimal(str(value))
            valid = number.is_finite() and number >= 0 and (field != "teacher_difficulty" or number <= 1)
        except (InvalidOperation, ValueError):
            valid = False
        if not valid:
            issues.append(f"{question_name} 的{label}无效")
    return issues


def _content(question):
    return {
        "text": question.text, "question_type": question.question_type,
        "answer": question.correct_answer, "accepted_answers": question.accepted_answers,
        "options": {option.key: option.text for option in question.options.all()},
        "teacher_difficulty": question.teacher_difficulty,
        "absolute_tolerance": question.absolute_tolerance,
        "relative_tolerance": question.relative_tolerance,
    }


def validate_question_answer_structure(question):
    issues = content_issues(_content(question), question_name=question.code or f"题目#{question.pk}")
    if issues:
        raise ValidationError(issues)


def readiness_issues(question, *, require_primary=None):
    issues = content_issues(_content(question), question_name=question.code or f"题目#{question.pk}")
    course_required = question.bank.course_id is not None
    if require_primary is None:
        require_primary = course_required
    if not course_required and not require_primary:
        return issues
    links = list(question.concept_links.select_related("concept", "parent_link__concept"))
    primary = [link for link in links if link.is_primary]
    if len(primary) != 1:
        issues.append("课程可用题目必须有且仅有一个主 KC。" if course_required else "题目缺少主知识点。")
        return issues
    if course_required:
        from .question_kc_mapping import validate_question_kc_mapping
        try:
            question._validate_active_course_mapping()
            if any(link.parent_link_id and link.parent_link.question_id != question.pk for link in links):
                raise ValidationError("题目 KC 的父节点必须属于当前题目。")
            validate_question_kc_mapping(question, root_kc_code=primary[0].concept.code, nodes=[
                {"kc_code": link.concept.code, "parent_kc_code": link.parent_link.concept.code if link.parent_link_id else None, "order": link.display_order}
                for link in links
            ])
        except ValidationError as exc:
            issues.extend(exc.messages)
    return list(dict.fromkeys(issues))


@transaction.atomic
def refresh_question_readiness(question):
    current = Question.objects.select_for_update().select_related("bank").prefetch_related("options").get(pk=question.pk)
    if current.status == Question.Status.ARCHIVED:
        question.status = current.status
        return []
    issues = readiness_issues(current)
    status = Question.Status.DRAFT if issues else Question.Status.ACTIVE
    if current.status != status:
        current.status = status
        current.save(update_fields=["status", "updated_at"])
    question.status = current.status
    return issues


@transaction.atomic
def recheck_bank_drafts(bank):
    result = {"checked": 0, "activated": 0, "draft": 0, "rows": []}
    for question in bank.questions.select_for_update().filter(status=Question.Status.DRAFT).order_by("pk"):
        issues = refresh_question_readiness(question)
        result["checked"] += 1
        result["activated" if question.status == Question.Status.ACTIVE else "draft"] += 1
        result["rows"].append({"id": question.pk, "code": question.code, "status": question.status, "draft_reasons": issues})
    return result
