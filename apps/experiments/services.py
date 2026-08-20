from __future__ import annotations
import hashlib
import random
import secrets
from collections import defaultdict, deque
from decimal import Decimal, InvalidOperation
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.accounts.class_lifecycle import lock_active_class_groups
from apps.question_bank.access import lock_question_bank
from apps.question_bank.models import Question, QuestionBank
from apps.question_bank.services.scoring import normalize_true_false_answer
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion, QuestionSnapshot


ARCHIVED_EXPERIMENT_BANK_MESSAGE = "题库已归档，不能同步或发布实验。"
SAMPLING_ALGORITHM_VERSION = "v1"
LEGACY_SAMPLING_ALGORITHM_VERSION = "legacy-v0"


def _choice_answers(value):
    if isinstance(value, str):
        values = value.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    return {str(item).strip().upper() for item in values if str(item).strip()}


def _has_text(value):
    return bool(" ".join(str(value or "").strip().split()))


def _validate_question_answer_structure(question):
    """Reject active questions that cannot be scored from an immutable snapshot."""
    options = {option.key.upper() for option in question.options.all()}
    answer = question.correct_answer
    question_name = question.code or f"题目#{question.pk}"

    if question.question_type == Question.Type.SINGLE:
        choices = _choice_answers(answer)
        if len(choices) != 1 or not choices <= options:
            raise ValidationError(f"{question_name} 的单选题答案必须对应且仅对应一个选项")
    elif question.question_type == Question.Type.MULTIPLE:
        choices = _choice_answers(answer)
        if len(choices) < 2 or not choices <= options:
            raise ValidationError(f"{question_name} 的多选题答案必须对应至少两个已有选项")
    elif question.question_type == Question.Type.NUMERIC:
        try:
            numeric_answer = Decimal(str(answer).strip())
        except (InvalidOperation, AttributeError, ValueError):
            raise ValidationError(f"{question_name} 的数值题答案必须是有效数字") from None
        if not numeric_answer.is_finite():
            raise ValidationError(f"{question_name} 的数值题答案必须是有效数字")
    elif question.question_type in {Question.Type.SHORT, Question.Type.FILL_BLANK}:
        if not _has_text(answer):
            raise ValidationError(f"{question_name} 的文本题答案不能为空")
        if not isinstance(question.accepted_answers, list) or any(
            not _has_text(item) for item in question.accepted_answers
        ):
            raise ValidationError(f"{question_name} 的可接受答案格式无效")
    elif question.question_type == Question.Type.TRUE_FALSE:
        if not isinstance(answer, bool) or normalize_true_false_answer(answer) is None:
            raise ValidationError(f"{question_name} 的判断题答案必须为正确或错误")
    else:
        raise ValidationError(f"{question_name} 的题型无效")

    if question.question_type not in {Question.Type.SINGLE, Question.Type.MULTIPLE} and options:
        raise ValidationError(f"{question_name} 的非选择题不能包含自定义选项")


def _publish_candidates(question_bank):
    return list(
        Question.objects.select_for_update()
        .filter(bank=question_bank, status=Question.Status.ACTIVE)
        .select_related("bank", "course_identity")
        .prefetch_related("options", "concept_links__concept")
        .order_by("sequence", "pk")
    )


def _invalid_identity_codes(questions):
    invalid = []
    for question in questions:
        identity = getattr(question, "course_identity", None)
        if question.bank.course_id is not None and (
            identity is None or identity.course_id != question.bank.course_id
        ):
            invalid.append(question.code or f"题目#{question.pk}")
    return invalid


def _raise_for_invalid_identities(questions):
    invalid_identities = _invalid_identity_codes(questions)
    if invalid_identities:
        raise ValidationError(
            f"以下课程题目缺少或不匹配永久题目标识：{', '.join(invalid_identities)}"
        )


def lock_experiment_question_bank(bank_or_pk, course=None):
    bank = lock_question_bank(bank_or_pk)
    if bank.visibility == QuestionBank.Visibility.ARCHIVED:
        raise ValidationError(ARCHIVED_EXPERIMENT_BANK_MESSAGE)
    if course is not None and bank.course_id != course.pk:
        raise ValidationError("实验与题库必须属于同一课程")
    return bank


def _lock_experiment_question_bank(experiment):
    return lock_experiment_question_bank(experiment.question_bank_id, experiment.course)


def validate_for_publish(experiment, question_bank=None):
    question_bank = question_bank or experiment.question_bank
    if question_bank.course_id != experiment.course_id:
        raise ValidationError("实验与题库必须属于同一课程")

    # Candidate discovery is deliberately fresh from the active bank.  Retain
    # the legacy link integrity check first, however, so a stale cross-course
    # link is diagnosed as a broken permanent identity rather than masked as
    # an empty candidate pool.
    linked_questions = [
        link.question
        for link in experiment.question_links.select_for_update().select_related(
            "question__bank", "question__course_identity"
        )
    ]
    _raise_for_invalid_identities(linked_questions)

    questions = _publish_candidates(question_bank)
    if not questions:
        raise ValidationError("题库中至少需要一道可用题目")
    _raise_for_invalid_identities(questions)
    missing = []
    for question in questions:
        links = list(question.concept_links.all())
        if not any(link.is_primary for link in links):
            missing.append(question.code)
        _validate_question_answer_structure(question)
    if missing:
        raise ValidationError(f"以下题目缺少主知识点：{', '.join(missing)}")
    if not experiment.student_links.exists():
        raise ValidationError("实验至少需要分配一名学生")
    return questions


def _quota_map(experiment):
    values = dict(
        experiment.question_quotas.select_for_update().values_list("question_type", "amount")
    )
    return {question_type: int(values.get(question_type, 0)) for question_type in Question.Type.values}


def _candidate_inventory(questions, selection_mode, quota_map):
    available = {question_type: 0 for question_type in Question.Type.values}
    for question in questions:
        available[question.question_type] += 1
    inventory = {
        question_type: {
            "need": quota_map[question_type]
            if selection_mode == Experiment.SelectionMode.QUOTA
            else available[question_type],
            "available": available[question_type],
        }
        for question_type in Question.Type.values
    }
    if selection_mode == Experiment.SelectionMode.QUOTA:
        insufficient = [
            (question_type, values)
            for question_type, values in inventory.items()
            if values["available"] < values["need"]
        ]
        if insufficient:
            labels = dict(Question.Type.choices)
            details = "; ".join(
                f"{labels[question_type]}：需要 {values['need']} 题，可用 {values['available']} 题"
                for question_type, values in insufficient
            )
            raise ValidationError(f"题库库存不足：{details}")
    return inventory


def selection_display_context(experiment):
    """Prepare stable, label-bearing selection details for teacher templates."""
    quota_map = {
        question_type: int(amount)
        for question_type, amount in experiment.question_quotas.values_list(
            "question_type", "amount"
        )
    }
    labels = dict(Question.Type.choices)
    quota_rows = [
        {
            "question_type": question_type,
            "label": labels[question_type],
            "amount": quota_map.get(question_type, 0),
        }
        for question_type in Question.Type.values
    ]
    active_counts = {question_type: 0 for question_type in Question.Type.values}
    for question_type in experiment.question_bank.questions.filter(
        status=Question.Status.ACTIVE
    ).values_list("question_type", flat=True):
        if question_type in active_counts:
            active_counts[question_type] += 1
    current_inventory_rows = [
        {
            "label": labels[question_type],
            "need": quota_map.get(question_type, 0)
            if experiment.selection_mode == Experiment.SelectionMode.QUOTA
            else active_counts[question_type],
            "available": active_counts[question_type],
        }
        for question_type in Question.Type.values
    ]
    version_rows = []
    for version in experiment.versions.all():
        configuration = version.configuration or {}
        inventory = configuration.get("candidate_inventory", {})
        version_rows.append(
            {
                "version": version,
                "candidate_count": configuration.get("candidate_count", version.question_snapshots.count()),
                "selection_mode": configuration.get("selection_mode", Experiment.SelectionMode.ALL),
                "inventory_rows": [
                    {
                        "label": labels[question_type],
                        "need": inventory.get(question_type, {}).get("need", 0),
                        "available": inventory.get(question_type, {}).get("available", 0),
                    }
                    for question_type in Question.Type.values
                ],
            }
        )
    return {
        "quota_rows": quota_rows,
        "current_candidate_count": sum(active_counts.values()),
        "current_inventory_rows": current_inventory_rows,
        "version_rows": version_rows,
    }

@transaction.atomic
def publish_experiment(experiment, actor):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    question_bank = _lock_experiment_question_bank(experiment)
    if experiment.status != Experiment.Status.DRAFT:
        raise ValidationError("只有草稿实验可以发布")
    questions = validate_for_publish(experiment, question_bank)
    quota_map = _quota_map(experiment)
    if (
        experiment.selection_mode == Experiment.SelectionMode.QUOTA
        and sum(quota_map.values()) <= 0
    ):
        raise ValidationError("按题型配额抽题至少需要抽取 1 道题")
    inventory = _candidate_inventory(questions, experiment.selection_mode, quota_map)
    version_number = (experiment.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1
    config = {
        "ordering_mode": experiment.ordering_mode, "feedback_mode": experiment.feedback_mode,
        "time_mode": experiment.time_mode, "total_time_limit_minutes": experiment.total_time_limit_minutes,
        "per_question_time_limit_seconds": experiment.per_question_time_limit_seconds,
        "resume_mode": experiment.resume_mode,
        "selection_mode": experiment.selection_mode,
        "quota_map": quota_map,
        "candidate_inventory": inventory,
        "candidate_count": len(questions),
        "sampling_salt": secrets.token_hex(32),
        "sampling_algorithm_version": SAMPLING_ALGORITHM_VERSION,
    }
    course_snapshot = {
        "course_name_snapshot": experiment.course.name if experiment.course_id else "",
        "course_code_snapshot": experiment.course.code if experiment.course_id else "",
        "course_term_snapshot": experiment.course.term if experiment.course_id else "",
    }
    version = ExperimentVersion.objects.create(
        experiment=experiment,
        version_number=version_number,
        configuration=config,
        published_by=actor,
        **course_snapshot,
    )
    for q in questions:
        identity = getattr(q, "course_identity", None)
        kc_links = list(q.concept_links.all())
        primary = next(item.concept.name for item in kc_links if item.is_primary)
        QuestionSnapshot.objects.create(
            version=version,
            source_question=q,
            source_sequence=q.sequence,
            course_sequence=identity.course_sequence if identity else None,
            stable_code=identity.stable_code if identity else "",
            source_bank_id=q.bank_id,
            source_bank_name=q.bank.name,
            bank_sequence=q.sequence,
            code=q.code,
            question_type=q.question_type, text=q.text,
            options=[{"key": item.key, "text": item.text} for item in q.options.all()],
            correct_answer=q.correct_answer, accepted_answers=q.accepted_answers,
            absolute_tolerance=q.absolute_tolerance, relative_tolerance=q.relative_tolerance,
            explanation=q.explanation, teacher_difficulty=q.teacher_difficulty,
            empirical_difficulty=q.empirical_difficulty, primary_kc=primary,
            all_kcs=[item.concept.name for item in kc_links],
        )
    experiment.status = Experiment.Status.PUBLISHED
    experiment.save(update_fields=["status", "updated_at"])
    return version


def seed_for(version_id, student_id):
    """Backward-compatible seed used by pre-quota callers."""
    return int(hashlib.sha256(f"{version_id}:{student_id}".encode()).hexdigest()[:16], 16)


def derive_session_seeds(version, student_id):
    configuration = version.configuration or {}
    salt = configuration.get("sampling_salt")
    if not salt:
        # Versions published before individualized sampling used seed_for() for
        # random ordering. Keep that exact stream when a legacy version first
        # receives a session after this migration.
        ordering_seed = hashlib.sha256(f"{version.pk}:{student_id}".encode()).hexdigest()
        sampling_seed = hashlib.sha256(
            f"legacy-version:{version.pk}:{student_id}:sample".encode()
        ).hexdigest()
        return (
            sampling_seed,
            ordering_seed,
            str(
                configuration.get("sampling_algorithm_version")
                or LEGACY_SAMPLING_ALGORITHM_VERSION
            ),
        )

    salt = str(salt)
    algorithm_version = str(
        configuration.get("sampling_algorithm_version") or SAMPLING_ALGORITHM_VERSION
    )

    def digest(purpose):
        return hashlib.sha256(f"{salt}:{student_id}:{purpose}".encode()).hexdigest()

    return digest("sample"), digest("order"), algorithm_version


def _snapshot_sort_key(snapshot):
    sequence = snapshot.bank_sequence if snapshot.bank_sequence is not None else snapshot.source_sequence
    return sequence, snapshot.source_sequence, snapshot.pk


def select_snapshots(version, student_id, sampling_seed=None):
    """Draw a student's immutable subset from the already-published candidate pool."""
    items = sorted(list(version.question_snapshots.all()), key=_snapshot_sort_key)
    configuration = version.configuration or {}
    if configuration.get("selection_mode", Experiment.SelectionMode.ALL) != Experiment.SelectionMode.QUOTA:
        return items

    sampling_seed = sampling_seed or derive_session_seeds(version, student_id)[0]
    rng = random.Random(int(sampling_seed[:16], 16))
    quota_map = configuration.get("quota_map", {})
    selected = []
    for question_type in Question.Type.values:
        amount = int(quota_map.get(question_type, 0) or 0)
        candidates = [item for item in items if item.question_type == question_type]
        if amount > len(candidates):
            labels = dict(Question.Type.choices)
            raise ValidationError(
                f"发布快照库存不足：{labels[question_type]}需要 {amount} 题，可用 {len(candidates)} 题"
            )
        selected.extend(rng.sample(candidates, amount))
    return selected


def ordered_snapshots(version, student_id, snapshots=None, ordering_seed=None):
    items = sorted(
        list(version.question_snapshots.all() if snapshots is None else snapshots),
        key=_snapshot_sort_key,
    )
    mode = version.configuration.get("ordering_mode", "fixed")
    ordering_seed = ordering_seed or derive_session_seeds(version, student_id)[1]
    rng = random.Random(int(ordering_seed[:16], 16))
    if mode == "random":
        rng.shuffle(items)
    elif mode == "stratified_random":
        groups = defaultdict(list)
        for item in items:
            difficulty = float(item.teacher_difficulty if item.teacher_difficulty is not None else 0.5)
            band = 0 if difficulty < 1/3 else 1 if difficulty < 2/3 else 2
            groups[(item.primary_kc, band)].append(item)
        queues = []
        for key in sorted(groups):
            rng.shuffle(groups[key]); queues.append(deque(groups[key]))
        rng.shuffle(queues)
        items = []
        while any(queues):
            for queue in queues:
                if queue: items.append(queue.popleft())
    return items

@transaction.atomic
def sync_experiment_questions(experiment):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    question_bank = _lock_experiment_question_bank(experiment)
    if experiment.status != Experiment.Status.DRAFT: raise ValidationError("发布后不能修改题目")
    experiment.question_links.all().delete()
    for idx, question in enumerate(question_bank.questions.filter(status="active"), start=1):
        ExperimentQuestion.objects.create(experiment=experiment, question=question, display_order=idx)

@transaction.atomic
def assign_students(experiment, students):
    if experiment.status != Experiment.Status.DRAFT: raise ValidationError("发布后不能修改学生")
    students = list(students)
    student_ids = [student.pk for student in students]
    if experiment.course_id is None:
        from apps.accounts.models import User

        student_class_ids = dict(
            User.objects.filter(pk__in=student_ids).values_list("pk", "class_group_id")
        )
        with lock_active_class_groups(
            student_class_ids.values(),
            inactive_error="不能分配停用班级中的学生",
        ) as locked_classes:
            experiment.student_links.all().delete()
            for student in students:
                class_group = locked_classes.get(student_class_ids[student.pk])
                ExperimentStudent.objects.create(
                    experiment=experiment,
                    student=student,
                    class_snapshot_name=str(class_group or ""),
                )
        return

    from apps.accounts.models import CourseEnrollment

    enrollments = {
        item["student_id"]: item
        for item in CourseEnrollment.objects.filter(
            course=experiment.course,
            student_id__in=student_ids,
            is_active=True,
        ).values("student_id", "class_group_id")
    }
    if len(enrollments) != len(students):
        raise ValidationError("只能分配当前课程可用班级中的在读学生")
    with lock_active_class_groups(
        [enrollment["class_group_id"] for enrollment in enrollments.values()],
        inactive_error="只能分配当前课程可用班级中的在读学生",
    ) as locked_classes:
        experiment.student_links.all().delete()
        for student in students:
            enrollment = enrollments[student.pk]
            ExperimentStudent.objects.create(
                experiment=experiment,
                student=student,
                class_snapshot_name=str(locked_classes[enrollment["class_group_id"]]),
            )
