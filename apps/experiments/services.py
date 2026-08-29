from __future__ import annotations
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import random
import secrets
from collections import defaultdict, deque
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.accounts.course_access import lock_course, lock_writable_course
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.accounts.class_lifecycle import lock_active_class_groups
from apps.audit.models import AuditLog
from apps.question_bank.access import lock_question_bank
from apps.question_bank.models import Question, QuestionBank
from apps.question_bank.services.numeric import normalize_finite_decimal
from apps.question_bank.services.scoring import normalize_true_false_answer
from apps.assessment.models import ExperimentSession
from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentParticipantClass,
    ExperimentParticipantOverride,
    ExperimentQuestion,
    ExperimentStudent,
    ExperimentClassSnapshot,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    QuestionSnapshot,
)


ARCHIVED_EXPERIMENT_BANK_MESSAGE = "题库已归档，不能同步或发布实验。"
SAMPLING_ALGORITHM_VERSION = "v1"
LEGACY_SAMPLING_ALGORITHM_VERSION = "legacy-v0"
_class_experiment_task_creation_scope = ContextVar(
    "class_experiment_task_creation_scope",
    default=False,
)


@contextmanager
def _class_experiment_task_publication_scope():
    token = _class_experiment_task_creation_scope.set(True)
    try:
        yield
    finally:
        _class_experiment_task_creation_scope.reset(token)


def _class_experiment_task_creation_is_active():
    return _class_experiment_task_creation_scope.get()


@dataclass(frozen=True)
class ParticipantRuleSaveResult:
    preview_count: int


def delete_experiment(experiment):
    """Delete an experiment and its immutable research tree in dependency order."""

    with transaction.atomic():
        experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
        if experiment.course_id is not None:
            lock_writable_course(experiment.course_id)
        if (
            ClassExperimentTask.objects.select_for_update()
            .filter(experiment=experiment)
            .exists()
        ):
            raise ValidationError("已存在正式班级实验任务，不能删除实验。")
        versions = list(
            ExperimentVersion.objects.select_for_update()
            .filter(experiment=experiment)
            .order_by("pk")
        )
        version_ids = [version.pk for version in versions]
        session_count = ExperimentSession.objects.filter(version_id__in=version_ids).count()
        snapshot_count = QuestionSnapshot.objects.filter(version_id__in=version_ids).count()
        ExperimentSession.objects.filter(version_id__in=version_ids).delete()
        QuestionSnapshot.objects.filter(version_id__in=version_ids).delete()
        ExperimentVersion.objects.filter(pk__in=version_ids).delete()
        details = {
            "experiment_id": experiment.pk,
            "course_id": experiment.course_id,
            "status": experiment.status,
            "version_count": len(version_ids),
            "session_count": session_count,
            "snapshot_count": snapshot_count,
        }
        experiment.delete()
        return details


def _participant_ids(values, label):
    ids = set()
    for value in values or []:
        value = getattr(value, "pk", value)
        if isinstance(value, bool):
            raise ValidationError(f"{label}包含无效对象。")
        try:
            value = int(value)
        except (TypeError, ValueError):
            raise ValidationError(f"{label}包含无效对象。")
        if value <= 0:
            raise ValidationError(f"{label}包含无效对象。")
        ids.add(value)
    return sorted(ids)


def _lock_draft_experiment(experiment, *, error_message):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.status != Experiment.Status.DRAFT:
        raise ValidationError(error_message)
    return experiment


def _lock_participant_course(experiment, *, action="修改实验参与规则"):
    if experiment.course_id is None:
        raise ValidationError("参与规则仅适用于课程实验。")
    course = lock_course(experiment.course_id)
    if course.status != Course.Status.ACTIVE:
        raise ValidationError(f"课程已归档，不能{action}。")
    return course


def _lock_course_classes(course):
    class_groups = list(
        ClassGroup.objects.select_for_update()
        .filter(course=course)
        .order_by("pk")
    )
    return {class_group.pk: class_group for class_group in class_groups}


def _locked_requested_classes(course, class_ids, locked_classes):
    selected_classes = [locked_classes.get(class_id) for class_id in class_ids]
    if any(class_group is None for class_group in selected_classes):
        missing_ids = [
            class_id
            for class_id, class_group in zip(class_ids, selected_classes)
            if class_group is None
        ]
        if ClassGroup.objects.filter(pk__in=missing_ids).exists():
            raise ValidationError("参与班级必须属于实验所属课程。")
        raise ValidationError("参与班级已变更，请刷新后重试。")
    for class_group in selected_classes:
        if not class_group.is_active:
            raise ValidationError("不能选择已停用的班级。")
    return {class_group.pk: class_group for class_group in selected_classes}


def _active_enrollments(course, student_ids):
    return {
        enrollment.student_id: enrollment
        for enrollment in CourseEnrollment.objects.select_for_update()
        .select_related("class_group", "student")
        .filter(
            course=course,
            student_id__in=student_ids,
            is_active=True,
            student__role=User.Role.STUDENT,
            student__is_active=True,
        )
        .order_by("pk")
    }


def _validate_current_enrollments(
    course,
    locked_classes,
    student_ids,
    *,
    error_message,
):
    enrollments = _active_enrollments(course, student_ids)
    if len(enrollments) != len(student_ids):
        raise ValidationError(error_message)
    if any(
        enrollment.class_group_id not in locked_classes
        or not locked_classes[enrollment.class_group_id].is_active
        for enrollment in enrollments.values()
    ):
        raise ValidationError(error_message)
    return enrollments


def _current_rule_roster(
    course,
    locked_classes,
    class_ids,
    include_ids,
    exclude_ids,
    *,
    include_error,
    validate_excludes,
):
    requested_ids = sorted(
        set(include_ids) | (set(exclude_ids) if validate_excludes else set())
    )
    requested_enrollments = _validate_current_enrollments(
        course,
        locked_classes,
        requested_ids,
        error_message=include_error,
    )
    if validate_excludes and any(
        requested_enrollments[student_id].class_group_id not in class_ids
        for student_id in exclude_ids
    ):
        raise ValidationError("只能排除已选参与班级中的学生。")

    class_enrollments = list(
        CourseEnrollment.objects.select_for_update()
        .select_related("class_group", "student")
        .filter(
            course=course,
            class_group_id__in=class_ids,
            is_active=True,
            student__role=User.Role.STUDENT,
            student__is_active=True,
        )
        .order_by("pk")
    )
    roster = {
        enrollment.student_id: enrollment
        for enrollment in class_enrollments
        if enrollment.class_group_id in locked_classes
    }
    roster.update({student_id: requested_enrollments[student_id] for student_id in include_ids})
    for student_id in exclude_ids:
        roster.pop(student_id, None)
    return roster


def _replace_experiment_students(experiment, roster):
    ExperimentStudent.objects.filter(experiment=experiment).delete()
    ExperimentStudent.objects.bulk_create(
        [
            ExperimentStudent(
                experiment=experiment,
                student_id=student_id,
                class_snapshot_name=str(enrollment.class_group),
            )
            for student_id, enrollment in sorted(roster.items())
        ]
    )


def _create_version_participant_snapshots(version, roster, published_class_groups):
    """Freeze native class membership at the same transaction boundary as publish."""
    class_snapshots = {}
    for class_group_id in sorted(published_class_groups):
        class_group = published_class_groups[class_group_id]
        class_snapshots[class_group_id] = ExperimentClassSnapshot.objects.create(
            version=version,
            source_class_group=class_group,
            name_snapshot=class_group.name,
            academic_year_snapshot=class_group.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
    ExperimentVersionParticipantSnapshot.objects.bulk_create(
        [
            ExperimentVersionParticipantSnapshot(
                version=version,
                student_id=student_id,
                class_snapshot=class_snapshots[enrollment.class_group_id],
            )
            for student_id, enrollment in sorted(roster.items())
        ]
    )
    return class_snapshots


def _create_class_experiment_tasks(experiment, version, class_snapshots):
    if not _class_experiment_task_creation_is_active():
        raise ValidationError("班级实验任务只能在实验发布事务中创建。")
    tasks = []
    for class_group_id in sorted(class_snapshots):
        task = ClassExperimentTask(
            class_group_id=class_group_id,
            experiment=experiment,
            release_snapshot=version,
            class_snapshot=class_snapshots[class_group_id],
            published_at=version.published_at,
        )
        task.save(force_insert=True)
        tasks.append(task)
    return tasks


def _create_legacy_version_participant_snapshots(version, experiment):
    """Preserve the old label exactly; do not infer a source ClassGroup."""
    snapshots_by_label = {}
    participants = ExperimentStudent.objects.filter(experiment=experiment).order_by("student_id")
    rows = []
    for participant in participants:
        label = participant.class_snapshot_name
        if label not in snapshots_by_label:
            snapshots_by_label[label] = ExperimentClassSnapshot.objects.create(
                version=version,
                name_snapshot=label,
                academic_year_snapshot="",
                capture_mode=(
                    ExperimentClassSnapshot.CaptureMode.LEGACY_LABEL_PROJECTION
                    if label
                    else ExperimentClassSnapshot.CaptureMode.UNRECORDED
                ),
            )
        rows.append(
            ExperimentVersionParticipantSnapshot(
                version=version,
                student_id=participant.student_id,
                class_snapshot=snapshots_by_label[label],
            )
        )
    ExperimentVersionParticipantSnapshot.objects.bulk_create(rows)


def _lock_materialized_participants(experiment):
    return list(
        ExperimentStudent.objects.select_for_update()
        .filter(experiment=experiment)
        .order_by("student_id")
        .values_list("pk", flat=True)
    )


def _publish_participant_roster(experiment, course, locked_classes):
    class_ids = list(
        experiment.participant_class_links.select_for_update()
        .order_by("class_group_id")
        .values_list("class_group_id", flat=True)
    )
    overrides = list(
        experiment.participant_override_links.select_for_update()
        .order_by("student_id")
        .values_list("student_id", "mode")
    )
    if not class_ids and not overrides:
        raise ValidationError("请先配置参与班级与学生。")

    _lock_materialized_participants(experiment)

    if any(
        class_id not in locked_classes or not locked_classes[class_id].is_active
        for class_id in class_ids
    ):
        raise ValidationError("所选参与班级已停用或已移出当前课程，不能发布实验。")

    include_ids = sorted(
        student_id
        for student_id, mode in overrides
        if mode == ExperimentParticipantOverride.Mode.INCLUDE
    )
    exclude_ids = sorted(
        student_id
        for student_id, mode in overrides
        if mode == ExperimentParticipantOverride.Mode.EXCLUDE
    )
    roster = _current_rule_roster(
        course,
        locked_classes,
        class_ids,
        include_ids,
        exclude_ids,
        include_error="显式纳入学生已退出当前课程或所在班级不可用，不能发布实验。",
        validate_excludes=False,
    )
    published_class_groups = {
        class_id: locked_classes[class_id]
        for class_id in class_ids
    }
    for enrollment in roster.values():
        published_class_groups[enrollment.class_group_id] = enrollment.class_group
    return roster, published_class_groups


def _locked_legacy_participant_count(experiment):
    initial_class_ids = list(
        ExperimentStudent.objects.filter(experiment=experiment)
        .order_by("student_id")
        .values_list("student__class_group_id", flat=True)
    )
    with lock_active_class_groups(
        initial_class_ids,
        inactive_error="不能发布停用班级中的学生。",
    ) as locked_classes:
        participant_links = list(
            ExperimentStudent.objects.select_for_update()
            .filter(experiment=experiment)
            .select_related("student")
            .order_by("student_id")
        )
        if any(
            link.student.class_group_id is not None
            and link.student.class_group_id not in locked_classes
            for link in participant_links
        ):
            raise ValidationError("学生班级已变更，请刷新后重试。")
    return len(participant_links)


@transaction.atomic
def save_participant_rules(experiment, classes, includes, excludes):
    """Atomically save a draft's rules and materialize its current roster preview."""

    class_ids = _participant_ids(classes, "参与班级")
    include_ids = _participant_ids(includes, "显式纳入学生")
    exclude_ids = _participant_ids(excludes, "排除学生")
    if set(include_ids) & set(exclude_ids):
        raise ValidationError("同一学生不能同时显式纳入和排除。")

    experiment = _lock_draft_experiment(
        experiment,
        error_message="发布后不能修改参与规则。",
    )
    return _save_participant_rules_locked(
        experiment,
        class_ids,
        include_ids,
        exclude_ids,
    )


def _save_participant_rules_locked(experiment, class_ids, include_ids, exclude_ids):
    course = _lock_participant_course(experiment)
    locked_classes = _lock_course_classes(course)
    selected_classes = _locked_requested_classes(course, class_ids, locked_classes)

    roster = _current_rule_roster(
        course,
        locked_classes,
        class_ids,
        include_ids,
        exclude_ids,
        include_error="学生必须是当前课程可用班级中的在读学生。",
        validate_excludes=True,
    )

    _lock_materialized_participants(experiment)
    ExperimentParticipantClass.objects.filter(experiment=experiment).delete()
    ExperimentParticipantOverride.objects.filter(experiment=experiment).delete()
    ExperimentParticipantClass.objects.bulk_create(
        [
            ExperimentParticipantClass(experiment=experiment, class_group=class_group)
            for class_group in selected_classes.values()
        ]
    )
    ExperimentParticipantOverride.objects.bulk_create(
        [
            ExperimentParticipantOverride(
                experiment=experiment,
                student_id=student_id,
                mode=ExperimentParticipantOverride.Mode.INCLUDE,
            )
            for student_id in include_ids
        ]
        + [
            ExperimentParticipantOverride(
                experiment=experiment,
                student_id=student_id,
                mode=ExperimentParticipantOverride.Mode.EXCLUDE,
            )
            for student_id in exclude_ids
        ]
    )
    _replace_experiment_students(experiment, roster)
    return ParticipantRuleSaveResult(preview_count=len(roster))


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
            normalize_finite_decimal(answer)
        except ValueError:
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
        Question.objects.select_for_update(of=("self",))
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


def validate_for_publish(experiment, question_bank=None, participant_count=None):
    question_bank = question_bank or experiment.question_bank
    if question_bank.course_id != experiment.course_id:
        raise ValidationError("实验与题库必须属于同一课程")

    # Candidate discovery is deliberately fresh from the active bank.  Retain
    # the legacy link integrity check first, however, so a stale cross-course
    # link is diagnosed as a broken permanent identity rather than masked as
    # an empty candidate pool.
    linked_questions = [
        link.question
        for link in experiment.question_links.select_for_update(of=("self",)).select_related(
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
    if participant_count is None:
        participant_count = experiment.student_links.count()
    if not participant_count:
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
    experiment = _lock_draft_experiment(
        experiment,
        error_message="只有草稿实验可以发布",
    )
    experiment.full_clean()
    course = None
    locked_classes = None
    if experiment.course_id is not None:
        course = _lock_participant_course(experiment, action="发布实验")
        locked_classes = _lock_course_classes(course)
    roster = None
    published_class_groups = None
    if course is not None:
        roster, published_class_groups = _publish_participant_roster(
            experiment,
            course,
            locked_classes,
        )
        participant_count = len(roster)
    else:
        participant_count = _locked_legacy_participant_count(experiment)
    question_bank = _lock_experiment_question_bank(experiment)
    questions = validate_for_publish(
        experiment,
        question_bank,
        participant_count=participant_count,
    )
    quota_map = _quota_map(experiment)
    if (
        experiment.selection_mode == Experiment.SelectionMode.QUOTA
        and sum(quota_map.values()) <= 0
    ):
        raise ValidationError("按题型配额抽题至少需要抽取 1 道题")
    inventory = _candidate_inventory(questions, experiment.selection_mode, quota_map)
    if roster is not None:
        _replace_experiment_students(experiment, roster)
    version_number = (experiment.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1
    config = {
        "ordering_mode": experiment.ordering_mode, "feedback_mode": experiment.feedback_mode,
        "time_mode": experiment.time_mode, "total_time_limit_minutes": experiment.total_time_limit_minutes,
        "per_question_time_limit_seconds": experiment.per_question_time_limit_seconds,
        "resume_mode": experiment.resume_mode,
        "assignment_unit_type": experiment.assignment_unit_type,
        "selection_mode": experiment.selection_mode,
        "quota_map": quota_map,
        "candidate_inventory": inventory,
        "candidate_count": len(questions),
        "sampling_salt": secrets.token_hex(32),
        "sampling_algorithm_version": SAMPLING_ALGORITHM_VERSION,
        "confidence_mode": experiment.confidence_mode,
        "confidence_sampling_unit": experiment.confidence_sampling_unit,
        "confidence_probability": (
            format(experiment.confidence_probability.normalize(), "f")
            if experiment.confidence_probability is not None
            else None
        ),
        "randomization_seed": experiment.confidence_randomization_seed or secrets.token_hex(32),
        "assignment_version": experiment.confidence_assignment_version,
        "confidence_scale_version": experiment.confidence_scale_version,
        "confidence_prompt_version": experiment.confidence_prompt_version,
        "heartbeat_interval_seconds": experiment.heartbeat_interval_seconds,
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
        assignment_unit_type=experiment.assignment_unit_type,
        published_by=actor,
        **course_snapshot,
    )
    class_tasks = []
    if roster is not None:
        class_snapshots = _create_version_participant_snapshots(
            version,
            roster,
            published_class_groups,
        )
        with _class_experiment_task_publication_scope():
            class_tasks = _create_class_experiment_tasks(
                experiment,
                version,
                class_snapshots,
            )
    else:
        _create_legacy_version_participant_snapshots(version, experiment)
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
    audit_db = version._state.db or "default"
    AuditLog.objects.using(audit_db).create(
        actor=actor,
        action="experiment.publish",
        object_type="Experiment",
        object_id=str(experiment.pk),
        details={
            "course_id": experiment.course_id,
            "experiment_id": experiment.pk,
            "release_snapshot_id": version.pk,
            "class_tasks": [
                {
                    "course_id": experiment.course_id,
                    "class_group_id": task.class_group_id,
                    "experiment_id": experiment.pk,
                    "task_id": task.pk,
                    "release_snapshot_id": version.pk,
                    "class_snapshot_id": str(task.class_snapshot_id),
                }
                for task in sorted(
                    class_tasks,
                    key=lambda task: (task.class_group_id, task.pk),
                )
            ],
        },
    )
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
    students = list(students)
    experiment = _lock_draft_experiment(
        experiment,
        error_message="发布后不能修改学生",
    )
    if experiment.course_id is not None:
        include_ids = _participant_ids(students, "显式纳入学生")
        return _save_participant_rules_locked(experiment, [], include_ids, [])

    student_ids = _participant_ids(students, "学生")
    initial_class_ids = dict(
        User.objects.filter(pk__in=student_ids).values_list("pk", "class_group_id")
    )
    if len(initial_class_ids) != len(student_ids):
        raise ValidationError("学生已变更，请刷新后重试。")
    with lock_active_class_groups(
        initial_class_ids.values(),
        inactive_error="不能分配停用班级中的学生",
    ) as locked_classes:
        locked_students = {
            student.pk: student
            for student in User.objects.select_for_update()
            .filter(pk__in=student_ids)
            .order_by("pk")
        }
        if len(locked_students) != len(student_ids) or any(
            locked_students[student_id].class_group_id is not None
            and locked_students[student_id].class_group_id not in locked_classes
            for student_id in student_ids
        ):
            raise ValidationError("学生班级已变更，请刷新后重试。")
        _lock_materialized_participants(experiment)
        ExperimentStudent.objects.filter(experiment=experiment).delete()
        for student_id in student_ids:
            class_group = locked_classes.get(locked_students[student_id].class_group_id)
            ExperimentStudent.objects.create(
                experiment=experiment,
                student_id=student_id,
                class_snapshot_name=str(class_group or ""),
            )
