"""Transactional services for the A+ class publication workflow."""

from __future__ import annotations

from datetime import timedelta, timezone as datetime_timezone
import hashlib
import hmac
import json
import random
import secrets

from django.core.exceptions import ValidationError
from django.conf import settings
from django.db import IntegrityError, connections, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import Course, CourseEnrollment, CourseSubject, User
from apps.question_bank.models import Concept, Question

from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassRandomizationIdentity,
    ExperimentClassSnapshot,
    ExperimentStudent,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    PublishPlan,
    PublishPlanClass,
    PublishPlanExclusion,
    PublishPreviewClassResult,
    PublishPreviewInvalidation,
    PublishPreviewItem,
    PublishPreviewParticipant,
    PublishPreviewRun,
    QuestionSnapshot,
    ReleaseSnapshotQuestionMapping,
    ScheduleRevision,
)
from .services import (
    _candidate_inventory,
    _class_experiment_task_publication_scope,
    _class_experiment_task_terminal_update_scope,
    _publish_candidates,
    _quota_map,
    validate_for_publish,
)
from .sampling import SamplingService, SamplingValidationError


PLAN_TTL = timedelta(hours=72)


class PublicationConflict(ValidationError):
    def __init__(self, message, *, code):
        self.code = code
        super().__init__(message, code=code)


def database_now(*, using="default"):
    """Return the database clock, normalized to an aware datetime.

    Publication expiry is a database fact. PostgreSQL's CURRENT_TIMESTAMP is
    fixed at transaction start, so use its wall clock for expiry decisions.
    """
    with connections[using].cursor() as cursor:
        if connections[using].vendor == "postgresql":
            cursor.execute("SELECT clock_timestamp()")
        else:
            cursor.execute("SELECT CURRENT_TIMESTAMP")
        value = cursor.fetchone()[0]
    if isinstance(value, str):
        value = parse_datetime(value.replace(" ", "T"))
    if value is None:
        return timezone.now()
    if timezone.is_naive(value):
        return timezone.make_aware(value, datetime_timezone.utc)
    return value


def _assert_plan_editor(plan, actor):
    if plan.current_editor_id and plan.current_editor_id != actor.pk:
        if not (actor.is_superuser or getattr(actor, "role", "") == "admin"):
            raise PublicationConflict("该发布计划正在由其他教师编辑。", code="PUBLISH_PLAN_LOCKED")


@transaction.atomic
def get_or_create_publish_plan(experiment, actor):
    """Resume the active plan or atomically retire a stale one before create."""
    course_id = Experiment.objects.values_list("course_id", flat=True).get(
        pk=experiment.pk
    )
    if course_id is not None:
        Course.objects.select_for_update().get(pk=course_id)
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.status == Experiment.Status.ARCHIVED:
        raise PublicationConflict("归档实验不能继续发布班级。", code="EXPERIMENT_ARCHIVED")
    if not experiment.sampling_mode:
        raise PublicationConflict(
            "旧版草稿必须先显式转换为总题数或知识点配额模式。",
            code="LEGACY_CONVERSION_REQUIRED",
        )
    now = database_now()
    active = (
        PublishPlan.objects.select_for_update()
        .filter(
            experiment=experiment,
            status__in=[PublishPlan.Status.DRAFT, PublishPlan.Status.PREVIEWED],
        )
        .order_by("pk")
        .first()
    )
    if active is not None and active.expires_at <= now:
        active.status = PublishPlan.Status.EXPIRED
        active.save(update_fields=["status"])
        active = None
    if active is not None:
        _assert_plan_editor(active, actor)
        if active.current_editor_id is None:
            active.current_editor = actor
            active.save(update_fields=["current_editor"])
        return active
    return PublishPlan.objects.create(
        experiment=experiment,
        created_by=actor,
        current_editor=actor,
        last_meaningful_write_at=now,
        expires_at=now + PLAN_TTL,
    )


def _lock_editable_publish_plan(plan, actor, *, expected_revision):
    plan = PublishPlan.objects.select_for_update().get(pk=plan.pk)
    now = database_now()
    if plan.status not in {PublishPlan.Status.DRAFT, PublishPlan.Status.PREVIEWED}:
        raise PublicationConflict("发布计划不可再修改。", code="PUBLISH_PLAN_EXPIRED")
    if plan.expires_at <= now:
        plan.status = PublishPlan.Status.EXPIRED
        plan.save(update_fields=["status"])
        raise PublicationConflict("发布计划已过期。", code="PUBLISH_PLAN_EXPIRED")
    _assert_plan_editor(plan, actor)
    if plan.plan_revision != expected_revision:
        raise PublicationConflict("发布计划已被更新，请刷新后重试。", code="PUBLISH_PLAN_STALE")
    return plan, now


def _touch_publish_plan(plan, now, *, current_step=None):
    plan.plan_revision += 1
    plan.last_meaningful_write_at = now
    plan.expires_at = now + PLAN_TTL
    if current_step is not None:
        plan.current_step = current_step
    plan.save(
        update_fields=[
            "plan_revision",
            "last_meaningful_write_at",
            "expires_at",
            "current_step",
        ]
    )
    return plan


@transaction.atomic
def revise_publish_plan(plan, actor, *, expected_revision, current_step=None):
    plan, now = _lock_editable_publish_plan(
        plan,
        actor,
        expected_revision=expected_revision,
    )
    return _touch_publish_plan(plan, now, current_step=current_step)


def _invalidate_current_preview(plan, *, reason, actor=None):
    if plan.preview_run_id is None:
        return
    run = PublishPreviewRun.objects.select_for_update().filter(pk=plan.preview_run_id).first()
    if run is not None and run.status == PublishPreviewRun.Status.VALID:
        run.status = PublishPreviewRun.Status.INVALIDATED
        run.invalidated_at = database_now()
        run.invalidation_reason = reason
        run.save(update_fields=["status", "invalidated_at", "invalidation_reason"])
        PublishPreviewInvalidation.objects.create(
            preview_run=run,
            reason=reason,
            created_by=actor,
        )
    plan.preview_run = None
    if plan.status == PublishPlan.Status.PREVIEWED:
        plan.status = PublishPlan.Status.DRAFT
    plan.save(update_fields=["preview_run", "status"])


def publish_plan_setup_projection(plan):
    class_plans = list(
        plan.class_plans.select_related("class_group").order_by("class_group_id")
    )
    if not class_plans:
        setup_status = "empty"
        missing_requirements = ["classes"]
    else:
        missing_requirements = []
        setup_status = "complete"
    blocking_issues = [
        {
            "code": "CLASS_NO_PARTICIPANTS",
            "message": f"班级“{plan_class.class_group}”没有可发布参与者。",
            "class_id": plan_class.class_group_id,
            "kc_id": None,
            "field": "classes",
        }
        for plan_class in class_plans
        if not _plan_participants(plan_class)
    ]
    if plan.experiment.sampling_mode and not plan.experiment.frozen_release_snapshot_id:
        try:
            SamplingService(plan.experiment).resolve()
        except SamplingValidationError as exc:
            blocking_issues.extend(exc.issues)
    return {
        "setup_status": setup_status,
        "missing_requirements": missing_requirements,
        "blocking_issues": blocking_issues,
        "field_errors": {},
        "plan_revision": plan.plan_revision,
        "expires_at": plan.expires_at,
    }


@transaction.atomic
def save_plan_selection(plan, actor, *, expected_revision, class_ids, exclusions_by_class):
    plan, now = _lock_editable_publish_plan(
        plan,
        actor,
        expected_revision=expected_revision,
    )
    plan = PublishPlan.objects.select_related("experiment").get(pk=plan.pk)
    experiment = plan.experiment
    class_ids = sorted({int(class_id) for class_id in class_ids})
    classes = list(
        experiment.course.classes.select_for_update()
        .filter(pk__in=class_ids, is_active=True)
        .order_by("pk")
    )
    if len(classes) != len(class_ids):
        raise ValidationError("只能选择本课程当前启用的班级。")
    if ClassExperimentTask.objects.select_for_update().filter(
        experiment=experiment,
        class_group_id__in=class_ids,
    ).exists():
        raise PublicationConflict("已发布班级不能再次选择。", code="TASK_ALREADY_EXISTS")
    planned_students = {}
    seen_students = {}
    for class_group in classes:
        student_ids = sorted(
            {int(student_id) for student_id in exclusions_by_class.get(class_group.pk, [])}
        )
        valid_students = set(
            CourseEnrollment.objects.filter(
                course=experiment.course,
                class_group=class_group,
                is_active=True,
                student__role=User.Role.STUDENT,
                student__is_active=True,
                student_id__in=student_ids,
            ).values_list("student_id", flat=True)
        )
        if valid_students != set(student_ids):
            raise ValidationError("排除名单包含不属于当前班级的学生。")
        students = list(
            CourseEnrollment.objects.filter(
                course=experiment.course,
                class_group=class_group,
                is_active=True,
                student__role=User.Role.STUDENT,
                student__is_active=True,
            )
            .exclude(student_id__in=student_ids)
            .select_related("student")
            .order_by("student_id")
        )
        for enrollment in students:
            prior_class = seen_students.get(enrollment.student_id)
            if prior_class is not None:
                raise PublicationConflict(
                    f"{enrollment.student.student_number} {enrollment.student.shown_name} "
                    f"同时属于“{prior_class}”和“{class_group}”，本实验只能纳入一次。",
                    code="PARTICIPANT_ALREADY_ASSIGNED",
                )
            seen_students[enrollment.student_id] = class_group
        _assert_participants_not_formally_assigned(experiment, students)
        planned_students[class_group.pk] = student_ids
    current_selection = {
        entry.class_group_id: tuple(
            entry.exclusions.order_by("student_id").values_list("student_id", flat=True)
        )
        for entry in plan.class_plans.prefetch_related("exclusions").order_by("class_group_id")
    }
    desired_selection = {
        class_group.pk: tuple(planned_students[class_group.pk])
        for class_group in classes
    }
    if current_selection == desired_selection:
        return plan
    plan = _touch_publish_plan(plan, now, current_step=1)
    _invalidate_current_preview(plan, reason="participants_changed", actor=actor)
    plan.class_plans.all().delete()
    for class_group in classes:
        plan_class = PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=class_group,
        )
        student_ids = planned_students[class_group.pk]
        PublishPlanExclusion.objects.bulk_create(
            [
                PublishPlanExclusion(publish_plan_class=plan_class, student_id=student_id)
                for student_id in student_ids
            ]
        )
    return plan


@transaction.atomic
def save_plan_schedules(
    plan,
    actor,
    *,
    expected_revision,
    default_opens_at,
    default_closes_at,
    overrides,
    configured_class_ids=None,
):
    plan, now = _lock_editable_publish_plan(
        plan,
        actor,
        expected_revision=expected_revision,
    )
    if default_opens_at and default_closes_at and default_closes_at <= default_opens_at:
        raise ValidationError("批量默认截止时间必须晚于开始时间。")
    plan_classes = list(plan.class_plans.select_for_update().order_by("pk"))
    if configured_class_ids is not None:
        configured_class_ids = {int(class_id) for class_id in configured_class_ids}
        selected_class_ids = {
            plan_class.class_group_id for plan_class in plan_classes
        }
        if not configured_class_ids.issubset(selected_class_ids):
            raise ValidationError("排期包含未选择的班级。")
    desired_schedules = {}
    for plan_class in plan_classes:
        override = overrides.get(plan_class.class_group_id)
        if override is None:
            desired_schedules[plan_class.pk] = (True, None, None)
            continue
        opens_at, closes_at = override
        if opens_at and closes_at and closes_at <= opens_at:
            raise ValidationError("班级截止时间必须晚于开始时间。")
        desired_schedules[plan_class.pk] = (False, opens_at, closes_at)
    schedule_changed = (
        plan.default_opens_at != default_opens_at
        or plan.default_closes_at != default_closes_at
        or any(
            (
                plan_class.use_batch_default,
                plan_class.opens_at,
                plan_class.closes_at,
            )
            != desired_schedules[plan_class.pk]
            for plan_class in plan_classes
        )
    )
    if not schedule_changed:
        return plan
    plan = _touch_publish_plan(plan, now, current_step=1)
    _invalidate_current_preview(plan, reason="schedule_changed", actor=actor)
    plan.default_opens_at = default_opens_at
    plan.default_closes_at = default_closes_at
    plan.save(update_fields=["default_opens_at", "default_closes_at"])
    for plan_class in plan_classes:
        (
            plan_class.use_batch_default,
            plan_class.opens_at,
            plan_class.closes_at,
        ) = desired_schedules[plan_class.pk]
        plan_class.save(
            update_fields=[
                "use_batch_default",
                "opens_at",
                "closes_at",
            ]
        )
    return plan


def canonical_json_hash(value):
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def question_release_fingerprint(question):
    """Fingerprint every question attribute that can alter a release fact."""
    options = [
        {"key": option.key, "text": option.text}
        for option in question.options.all().order_by("pk")
    ]
    concept_links = list(question.concept_links.select_related("concept").order_by("pk"))
    return canonical_json_hash(
        {
            "source_question_id": question.pk,
            "question_type": question.question_type,
            "text": question.text,
            "options": options,
            "correct_answer": question.correct_answer,
            "accepted_answers": question.accepted_answers,
            "absolute_tolerance": question.absolute_tolerance,
            "relative_tolerance": question.relative_tolerance,
            "explanation": question.explanation,
            "teacher_difficulty": question.teacher_difficulty,
            "empirical_difficulty": question.empirical_difficulty,
            "knowledge_components": [
                {"name": link.concept.name, "is_primary": link.is_primary}
                for link in concept_links
            ],
            "bank_id": question.bank_id,
        }
    )


def _seed_mask(experiment_id, class_group_id):
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        f"experiment-class-root:{experiment_id}:{class_group_id}".encode("utf-8"),
        hashlib.sha256,
    ).digest()


def _encrypt_root_seed(seed, experiment_id, class_group_id):
    mask = _seed_mask(experiment_id, class_group_id)
    return bytes(left ^ right for left, right in zip(seed, mask))


def _decrypt_root_seed(identity):
    mask = _seed_mask(identity.experiment_id, identity.class_group_id)
    return bytes(
        left ^ right for left, right in zip(bytes(identity.encrypted_root_seed), mask)
    )


@transaction.atomic
def get_or_create_randomization_identity(experiment, class_group, actor):
    identity = (
        ExperimentClassRandomizationIdentity.objects.select_for_update()
        .filter(experiment=experiment, class_group=class_group)
        .first()
    )
    if identity is not None:
        return identity
    root_seed = secrets.token_bytes(32)
    try:
        with transaction.atomic():
            return ExperimentClassRandomizationIdentity.objects.create(
                experiment=experiment,
                class_group=class_group,
                encrypted_root_seed=_encrypt_root_seed(root_seed, experiment.pk, class_group.pk),
                root_seed_fingerprint=hashlib.sha256(root_seed).hexdigest(),
                created_by=actor,
            )
    except IntegrityError:
        return ExperimentClassRandomizationIdentity.objects.get(
            experiment=experiment,
            class_group=class_group,
        )


def randomization_subject_key(subject):
    return hashlib.sha256(
        f"course-subject:{subject.public_uuid}".encode("utf-8")
    ).hexdigest()


def _derive_seed(root_seed, domain, subject_key):
    return hmac.new(
        root_seed,
        f"{domain}:{subject_key}".encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _plan_participants(plan_class):
    excluded_ids = set(plan_class.exclusions.values_list("student_id", flat=True))
    return list(
        CourseEnrollment.objects.filter(
            course_id=plan_class.publish_plan.experiment.course_id,
            class_group_id=plan_class.class_group_id,
            is_active=True,
            student__role=User.Role.STUDENT,
            student__is_active=True,
        )
        .exclude(student_id__in=excluded_ids)
        .select_related("student")
        .order_by("student__student_number", "student_id")
    )


def effective_plan_schedule(plan_class):
    if plan_class.use_batch_default:
        plan = plan_class.publish_plan
        return plan.default_opens_at, plan.default_closes_at
    return plan_class.opens_at, plan_class.closes_at


def class_task_business_status(task, *, now=None):
    from apps.assessment.deadline_services import DeadlineDecisionService

    return DeadlineDecisionService.get_task_state(task, at=now or database_now())


def _assert_participants_not_formally_assigned(experiment, students):
    student_ids = [getattr(student, "pk", student) for student in students]
    conflicts = (
        ExperimentVersionParticipantSnapshot.objects.filter(
            version__experiment=experiment,
            class_task__isnull=False,
            student_id__in=student_ids,
        )
        .select_related("student", "class_task__class_group")
        .order_by("student_id", "class_task_id")
    )
    if conflicts.exists():
        conflict = conflicts.first()
        raise PublicationConflict(
            f"{conflict.student.student_number} {conflict.student.shown_name} 已被本实验的“{conflict.class_task.class_group}”任务纳入，不能再次参与。",
            code="PARTICIPANT_ALREADY_ASSIGNED",
        )


def _candidate_source_question(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.source_question
    return candidate


def _candidate_id(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.source_question_id
    return candidate.pk


def _candidate_sequence(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.question_snapshot.source_sequence
    return candidate.sequence


def _candidate_question_type(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.question_snapshot.question_type
    return candidate.question_type


def _candidate_primary_kc(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.question_snapshot.primary_kc
    return next(
        (link.concept.name for link in candidate.concept_links.all() if link.is_primary),
        "",
    )


def _candidate_teacher_difficulty(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.question_snapshot.teacher_difficulty
    return candidate.teacher_difficulty


def _candidate_release_fingerprint(candidate):
    if isinstance(candidate, ReleaseSnapshotQuestionMapping):
        return candidate.question_release_fingerprint
    return question_release_fingerprint(candidate)


def _frozen_quota_selection(items, configuration, sampling_seed):
    rng = random.Random(int(sampling_seed[:16], 16))
    rules = list(configuration["sampling_kcs"])
    evidence = {
        row["question_id"]: row
        for row in configuration.get("candidate_resolution", [])
    }
    slots = [
        (row["kc_code"], index)
        for row in rules
        for index in range(row.get("quota") or 0)
    ]
    rng.shuffle(slots)
    candidate_ids = {}
    by_id = {_candidate_id(item): item for item in items}
    for rule in rules:
        ids = [
            item_id
            for item_id in by_id
            if rule["kc_code"]
            in evidence.get(item_id, {}).get("matched_kc_codes", [])
        ]
        rng.shuffle(ids)
        candidate_ids[rule["kc_code"]] = ids
    question_to_slot = {}
    slot_to_question = {}

    def augment(slot, visited):
        for question_id in candidate_ids[slot[0]]:
            if question_id in visited:
                continue
            visited.add(question_id)
            previous = question_to_slot.get(question_id)
            if previous is None or augment(previous, visited):
                question_to_slot[question_id] = slot
                slot_to_question[slot] = question_id
                if previous is not None and slot_to_question.get(previous) == question_id:
                    slot_to_question.pop(previous, None)
                return True
        return False

    for slot in slots:
        augment(slot, set())
    if len(slot_to_question) != len(slots):
        raise PublicationConflict(
            "冻结候选图无法同时满足全部 KC 配额。",
            code="KC_QUOTA_GLOBAL_INSUFFICIENT",
        )
    rule_by_code = {row["kc_code"]: row for row in rules}
    return [
        {
            "candidate": by_id[question_id],
            "sampling_source": {
                "mode": Experiment.SamplingMode.KC_QUOTA,
                "quota_kc_code": kc_code,
                "match_scope": rule_by_code[kc_code]["match_scope"],
                "algorithm_version": "deterministic-bipartite-v2",
            },
        }
        for (kc_code, _slot), question_id in slot_to_question.items()
    ]


def _select_questions(experiment, candidates, sampling_seed, ordering_seed):
    items = sorted(
        candidates,
        key=lambda item: (_candidate_sequence(item), _candidate_id(item)),
    )
    configuration = (
        items[0].release_snapshot.configuration
        if items and isinstance(items[0], ReleaseSnapshotQuestionMapping)
        else None
    )
    sampling_mode = (
        configuration.get("sampling_mode")
        if configuration is not None
        else experiment.sampling_mode
    )
    if sampling_mode:
        if configuration is not None:
            rng = random.Random(int(sampling_seed[:16], 16))
            if configuration["sampling_mode"] == Experiment.SamplingMode.TOTAL_COUNT:
                type_quotas = configuration.get("sampling_type_quotas", [])
                if type_quotas:
                    selected = []
                    for quota in type_quotas:
                        options = [
                            item
                            for item in items
                            if _candidate_question_type(item)
                            == quota["question_type"]
                        ]
                        if len(options) < quota["amount"]:
                            raise PublicationConflict(
                                "冻结候选集无法满足题型配额。",
                                code="QUESTION_TYPE_QUOTA_INSUFFICIENT",
                            )
                        selected.extend(rng.sample(options, quota["amount"]))
                    items = [
                        {
                            "candidate": item,
                            "sampling_source": {
                                "mode": Experiment.SamplingMode.TOTAL_COUNT,
                                "quota_kc_code": None,
                                "quota_question_type": _candidate_question_type(item),
                                "match_scope": configuration["sampling_match_scope"],
                                "algorithm_version": "deterministic-union-type-quota-v3",
                            },
                        }
                        for item in selected
                    ]
                else:
                    items = [
                        {
                            "candidate": item,
                            "sampling_source": {
                                "mode": Experiment.SamplingMode.TOTAL_COUNT,
                                "quota_kc_code": None,
                                "quota_question_type": None,
                                "match_scope": configuration["sampling_match_scope"],
                                "algorithm_version": "deterministic-union-sample-v2",
                            },
                        }
                        for item in rng.sample(items, configuration["sampling_total_count"])
                    ]
            else:
                items = _frozen_quota_selection(
                    items, configuration, sampling_seed
                )
        else:
            rules_by_id = {
                rule.concept_id: rule
                for rule in experiment.sampling_kcs.select_related("concept")
            }
            items = [
                {
                    "candidate": row["question"],
                    "sampling_source": {
                        "mode": experiment.sampling_mode,
                        "quota_kc_code": (
                            rules_by_id[row["quota_kc_id"]].concept.code
                            if row["quota_kc_id"] is not None
                            else None
                        ),
                        "quota_question_type": row["quota_question_type"],
                        "match_scope": row["match_scope"],
                        "algorithm_version": (
                            "deterministic-union-type-quota-v3"
                            if row["quota_question_type"] is not None
                            else (
                                "deterministic-union-sample-v2"
                                if experiment.sampling_mode
                                == Experiment.SamplingMode.TOTAL_COUNT
                                else "deterministic-bipartite-v2"
                            )
                        ),
                    },
                }
                for row in SamplingService(experiment).select_with_attribution(
                    sampling_seed
                )
            ]
    elif experiment.selection_mode == Experiment.SelectionMode.QUOTA:
        rng = random.Random(int(sampling_seed[:16], 16))
        quota_map = _quota_map(experiment)
        selected = []
        for question_type in Question.Type.values:
            options = [
                item
                for item in items
                if _candidate_question_type(item) == question_type
            ]
            selected.extend(rng.sample(options, int(quota_map.get(question_type, 0))))
        items = selected
    if not sampling_mode:
        items = [{"candidate": item, "sampling_source": {}} for item in items]
    items = sorted(
        items,
        key=lambda row: (
            _candidate_sequence(row["candidate"]),
            _candidate_id(row["candidate"]),
        ),
    )
    rng = random.Random(int(ordering_seed[:16], 16))
    ordering_mode = (
        configuration.get("ordering_mode")
        if configuration is not None
        else experiment.ordering_mode
    )
    if ordering_mode == Experiment.Ordering.RANDOM:
        rng.shuffle(items)
    elif ordering_mode == Experiment.Ordering.STRATIFIED:
        groups = {}
        for item in items:
            primary = _candidate_primary_kc(item["candidate"])
            difficulty = float(_candidate_teacher_difficulty(item["candidate"]) or 0.5)
            band = 0 if difficulty < 1 / 3 else 1 if difficulty < 2 / 3 else 2
            groups.setdefault((primary, band), []).append(item)
        queues = []
        for key in sorted(groups):
            rng.shuffle(groups[key])
            queues.append(groups[key])
        items = []
        while any(queues):
            for queue in queues:
                if queue:
                    items.append(queue.pop())
    return items


def _current_release(experiment):
    if experiment.frozen_release_snapshot_id:
        return experiment.frozen_release_snapshot
    return None


def _preview_candidates(experiment):
    release = _current_release(experiment)
    if release is None:
        validate_for_publish(experiment, experiment.question_bank, participant_count=1)
        if experiment.sampling_mode:
            candidates = SamplingService(experiment).resolve().candidates
        else:
            candidates = _publish_candidates(experiment.question_bank)
            _candidate_inventory(candidates, experiment.selection_mode, _quota_map(experiment))
        return candidates
    mappings = list(
        ReleaseSnapshotQuestionMapping.objects.filter(release_snapshot=release)
        .select_related("source_question", "question_snapshot")
        .order_by("source_question_id")
    )
    if not mappings:
        raise PublicationConflict("历史发布快照缺少可验证题目映射。", code="PREVIEW_RELEASE_MISMATCH")
    return mappings


def _preview_definition_fingerprint(experiment, candidates):
    from apps.question_bank.services.question_kc_mapping import (
        project_question_kc_mapping,
    )

    release = _current_release(experiment)
    frozen = release.configuration if release is not None else None
    if frozen is not None:
        sampling_kcs = frozen.get("sampling_kcs", [])
    elif experiment.sampling_mode:
        SamplingService(experiment).resolve()
        sampling_kcs = [
            {
                "kc_code": rule.concept.code,
                "quota": rule.quota,
                "match_scope": rule.match_scope,
            }
            for rule in experiment.sampling_kcs.select_related("concept").order_by(
                "concept__code"
            )
        ]
    else:
        sampling_kcs = []
    return canonical_json_hash(
        {
            "canonicalization_version": "canonical-json-v1",
            "experiment": experiment.pk,
            "status": experiment.status,
            "question_bank": experiment.question_bank_id,
            "questions": [
                {
                    "id": _candidate_id(candidate),
                    "fingerprint": _candidate_release_fingerprint(candidate),
                    "mapping_hash": (
                        candidate.question_snapshot.kc_mapping_hash
                        if isinstance(candidate, ReleaseSnapshotQuestionMapping)
                        else project_question_kc_mapping(candidate)["mapping_hash"]
                    ),
                }
                for candidate in candidates
            ],
            "selection_mode": frozen.get("selection_mode") if frozen else experiment.selection_mode,
            "sampling_mode": frozen.get("sampling_mode") if frozen else experiment.sampling_mode,
            "sampling_total_count": frozen.get("sampling_total_count") if frozen else experiment.sampling_total_count,
            "sampling_match_scope": frozen.get("sampling_match_scope") if frozen else experiment.sampling_match_scope,
            "sampling_kcs": sampling_kcs,
            "ordering_mode": frozen.get("ordering_mode") if frozen else experiment.ordering_mode,
            "feedback_mode": frozen.get("feedback_mode") if frozen else experiment.feedback_mode,
            "research_tools": frozen.get("research_tools", {}) if frozen else experiment.research_tools,
            "resume_mode": frozen.get("resume_mode") if frozen else experiment.resume_mode,
            "assignment_unit_type": frozen.get("assignment_unit_type") if frozen else experiment.assignment_unit_type,
            "total_time_limit_minutes": frozen.get("total_time_limit_minutes") if frozen else experiment.total_time_limit_minutes,
            "per_question_time_limit_seconds": frozen.get("per_question_time_limit_seconds") if frozen else experiment.per_question_time_limit_seconds,
            "quotas": frozen.get("quota_map") if frozen else _quota_map(experiment),
            "confidence_mode": frozen.get("confidence_mode") if frozen else experiment.confidence_mode,
            "confidence_sampling_unit": frozen.get("confidence_sampling_unit") if frozen else experiment.confidence_sampling_unit,
            "confidence_probability": frozen.get("confidence_probability") if frozen else str(experiment.confidence_probability or ""),
            "timer_semantics_version": (
                release.timer_semantics_version
                if release is not None
                else "continuous_wall_clock_v2"
            ),
            "randomization_semantics_version": (
                release.randomization_semantics_version
                if release is not None
                else "task_root_hmac_v2"
            ),
        }
    )


@transaction.atomic
def generate_preview(plan, actor, *, expected_revision):
    plan_identity = PublishPlan.objects.values(
        "experiment_id", "experiment__course_id"
    ).get(pk=plan.pk)
    course_id = plan_identity["experiment__course_id"]
    if course_id is not None:
        Course.objects.select_for_update().get(pk=course_id)
    experiment = Experiment.objects.select_for_update().get(
        pk=plan_identity["experiment_id"]
    )
    plan, _ = _lock_editable_publish_plan(
        plan,
        actor,
        expected_revision=expected_revision,
    )
    plan.experiment = experiment
    if experiment.status == Experiment.Status.ARCHIVED:
        raise PublicationConflict("归档实验不能继续发布班级。", code="EXPERIMENT_ARCHIVED")
    plan_classes = list(
        PublishPlanClass.objects.select_for_update()
        .filter(publish_plan=plan)
        .select_related("class_group")
        .order_by("class_group_id")
    )
    if not plan_classes:
        raise ValidationError("请至少选择一个尚未发布班级。")
    setup = publish_plan_setup_projection(plan)
    if setup["setup_status"] != "complete":
        raise ValidationError("请先为所有已选班级确认开放排期。")
    if setup["blocking_issues"]:
        raise ValidationError(
            [issue["message"] for issue in setup["blocking_issues"]]
        )
    candidates = _preview_candidates(experiment)
    definition_fingerprint = _preview_definition_fingerprint(experiment, candidates)
    class_inputs = []
    preview_payload = []
    run = PublishPreviewRun.objects.create(
        publish_plan=plan,
        plan_revision=plan.plan_revision,
        definition_fingerprint=definition_fingerprint,
        input_hash="",
        output_hash="",
        created_by=actor,
    )
    seen_students = {}
    for plan_class in plan_classes:
        if plan_class.class_group.course_id != experiment.course_id:
            raise ValidationError("发布班级必须属于实验所属课程。")
        if ClassExperimentTask.objects.select_for_update().filter(
            experiment=experiment,
            class_group=plan_class.class_group,
        ).exists():
            raise PublicationConflict("该班级已存在本实验的正式任务。", code="TASK_ALREADY_EXISTS")
        enrollments = _plan_participants(plan_class)
        students = [enrollment.student for enrollment in enrollments]
        if not students:
            raise ValidationError(f"班级“{plan_class.class_group}”没有可发布参与者。")
        _assert_participants_not_formally_assigned(experiment, students)
        for student in students:
            prior_class = seen_students.get(student.pk)
            if prior_class is not None:
                raise PublicationConflict(
                    f"{student.student_number} {student.shown_name} "
                    f"同时属于“{prior_class}”和“{plan_class.class_group}”，本实验只能纳入一次。",
                    code="PARTICIPANT_ALREADY_ASSIGNED",
                )
            seen_students[student.pk] = plan_class.class_group
        identity = get_or_create_randomization_identity(experiment, plan_class.class_group, actor)
        root_seed = _decrypt_root_seed(identity)
        opens_at, closes_at = effective_plan_schedule(plan_class)
        class_input = {
            "class_group": plan_class.class_group_id,
            "identity": identity.root_seed_fingerprint,
            "participants": [student.pk for student in students],
            "schedule": {
                "opens_at": opens_at.isoformat() if opens_at is not None else None,
                "closes_at": closes_at.isoformat() if closes_at is not None else None,
            },
        }
        class_inputs.append(class_input)
        class_result = PublishPreviewClassResult.objects.create(
            preview_run=run,
            publish_plan_class=plan_class,
            randomization_identity=identity,
            participant_count=len(students),
            input_hash=canonical_json_hash(class_input),
            output_hash="",
        )
        result_payload = []
        for student in students:
            subject, _ = CourseSubject.objects.get_or_create(
                course_id=experiment.course_id,
                student=student,
            )
            subject_key = randomization_subject_key(subject)
            sampling_seed = _derive_seed(root_seed, "sampling:v2", subject_key)
            ordering_seed = _derive_seed(root_seed, "ordering:v2", subject_key)
            selected = _select_questions(experiment, candidates, sampling_seed, ordering_seed)
            participant = PublishPreviewParticipant.objects.create(
                class_result=class_result,
                student=student,
                course_subject=subject,
                randomization_subject_key=subject_key,
                sampling_seed=sampling_seed,
                ordering_seed=ordering_seed,
                sampling_algorithm_version="task_root_hmac_v2",
                item_count=len(selected),
            )
            items_payload = []
            for index, selection in enumerate(selected, start=1):
                candidate = selection["candidate"]
                source_question = _candidate_source_question(candidate)
                fingerprint = _candidate_release_fingerprint(candidate)
                PublishPreviewItem.objects.create(
                    participant=participant,
                    source_question=source_question,
                    question_release_fingerprint=fingerprint,
                    assigned_sequence_index=index,
                    sampling_source=selection["sampling_source"],
                )
                items_payload.append(
                    (source_question.pk, fingerprint, selection["sampling_source"])
                )
            result_payload.append((student.pk, items_payload))
        class_result.output_hash = canonical_json_hash(result_payload)
        class_result.save(update_fields=["output_hash"])
        preview_payload.append((plan_class.class_group_id, result_payload))
    run.input_hash = canonical_json_hash(class_inputs)
    run.output_hash = canonical_json_hash(preview_payload)
    run.save(update_fields=["input_hash", "output_hash"])
    plan.status = PublishPlan.Status.PREVIEWED
    plan.preview_run = run
    plan.current_step = 2
    plan.save(update_fields=["status", "preview_run", "current_step"])
    return run


def _release_configuration(experiment, candidates):
    from .research_tool_config import validate_research_tools
    configuration = {
        "research_tools": validate_research_tools(experiment.research_tools),
        "ordering_mode": experiment.ordering_mode,
        "feedback_mode": experiment.feedback_mode,
        "feedback_release_policy": experiment.feedback_release_policy,
        "time_mode": experiment.time_mode,
        "total_time_limit_minutes": experiment.total_time_limit_minutes,
        "per_question_time_limit_seconds": experiment.per_question_time_limit_seconds,
        "resume_mode": experiment.resume_mode,
        "assignment_unit_type": experiment.assignment_unit_type,
        "selection_mode": experiment.selection_mode,
        "quota_map": _quota_map(experiment),
        "candidate_count": len(candidates),
        "candidate_fingerprints": {
            str(_candidate_id(candidate)): _candidate_release_fingerprint(candidate)
            for candidate in candidates
        },
        "confidence_mode": experiment.confidence_mode,
        "confidence_sampling_unit": experiment.confidence_sampling_unit,
        "confidence_probability": str(experiment.confidence_probability or ""),
        "confidence_scale_version": experiment.confidence_scale_version,
        "heartbeat_interval_seconds": experiment.heartbeat_interval_seconds,
        "timezone": settings.TIME_ZONE,
    }
    if experiment.sampling_mode:
        from apps.question_bank.services.question_kc_mapping import (
            project_question_kc_mapping,
        )

        resolution = SamplingService(experiment).resolve()
        quota_map = _quota_map(experiment)
        sampling_type_quotas = (
            [
                {"question_type": question_type, "amount": quota_map[question_type]}
                for question_type in Question.Type.values
                if quota_map.get(question_type, 0) > 0
            ]
            if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
            else []
        )
        configuration["sampling_mode"] = experiment.sampling_mode
        configuration["sampling_total_count"] = experiment.sampling_total_count
        configuration["sampling_match_scope"] = experiment.sampling_match_scope
        configuration["sampling_type_quotas"] = sampling_type_quotas
        configuration["sampling_kcs"] = [
            {
                "kc_code": rule.concept.code,
                "kc_name": rule.concept.name,
                "quota": rule.quota,
                "match_scope": rule.match_scope,
            }
            for rule in experiment.sampling_kcs.select_related("concept").order_by(
                "concept__code"
            )
        ]
        candidate_resolution = []
        for question in candidates:
            mapping = project_question_kc_mapping(question)
            all_codes = {node["kc_code"] for node in mapping["nodes"]}
            matched_nodes = []
            for rule in experiment.sampling_kcs.select_related("concept").order_by(
                "concept__code"
            ):
                scope = (
                    experiment.sampling_match_scope
                    if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
                    else rule.match_scope
                )
                if (
                    mapping["root_kc_code"] == rule.concept.code
                    if scope == Experiment.MatchScope.PRIMARY
                    else rule.concept.code in all_codes
                ):
                    matched_nodes.append(rule.concept.code)
            candidate_resolution.append(
                {
                    "question_id": question.pk,
                    "stable_question_code": (
                        question.course_identity.stable_code
                        if getattr(question, "course_identity", None) is not None
                        else question.code
                    ),
                    "mapping_hash": mapping["mapping_hash"],
                    "question_type": question.question_type,
                    "root_kc_code": mapping["root_kc_code"],
                    "matched_kc_codes": matched_nodes,
                }
            )
        configuration["candidate_resolution"] = candidate_resolution
        configuration["sampling_inventory"] = [
            {
                "kc_code": rule.concept.code,
                "kc_name": rule.concept.name,
                "quota": rule.quota,
                "match_scope": (
                    experiment.sampling_match_scope
                    if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
                    else rule.match_scope
                ),
                "available": (
                    len(resolution.buckets[rule.concept_id])
                    if experiment.sampling_mode == Experiment.SamplingMode.KC_QUOTA
                    else sum(
                        1
                        for evidence in candidate_resolution
                        if rule.concept.code in evidence["matched_kc_codes"]
                    )
                ),
            }
            for rule in experiment.sampling_kcs.select_related("concept").order_by(
                "concept__code"
            )
        ]
        sampling_type_inventory = (
            [
                {
                    "question_type": question_type,
                    "label": dict(Question.Type.choices)[question_type],
                    "requested": quota_map.get(question_type, 0),
                    "available": len(resolution.type_buckets.get(question_type, [])),
                }
                for question_type in Question.Type.values
            ]
            if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
            else []
        )
        configuration["sampling"] = {
            "schema_version": (
                "sampling-snapshot-v3"
                if sampling_type_quotas
                else "sampling-snapshot-v2"
            ),
            "mode": configuration["sampling_mode"],
            "total_count": configuration["sampling_total_count"],
            "match_scope": configuration["sampling_match_scope"],
            "selected_nodes": configuration["sampling_kcs"],
            "type_quotas": sampling_type_quotas,
            "type_inventory": sampling_type_inventory,
            "attribution_rule": (
                "deduplicated_question_union"
                if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
                else "participant_local_global_bipartite_matching"
            ),
            "candidate_resolution": configuration["candidate_resolution"],
            "inventory": configuration["sampling_inventory"],
            "sampling_algorithm_version": (
                "deterministic-union-type-quota-v3"
                if sampling_type_quotas
                else (
                    "deterministic-union-sample-v2"
                    if experiment.sampling_mode == Experiment.SamplingMode.TOTAL_COUNT
                    else "deterministic-bipartite-v2"
                )
            ),
            "candidate_ordering": "source_sequence_then_pk",
            "seed_derivation_version": "task_root_hmac_v2",
        }
    return configuration


def _create_question_snapshot(version, question):
    from apps.question_bank.services.question_kc_mapping import (
        project_question_kc_mapping,
    )

    concept_links = list(question.concept_links.select_related("concept").order_by("pk"))
    primary = next((link.concept.name for link in concept_links if link.is_primary), "")
    primary_link = next((link for link in concept_links if link.is_primary), None)

    def concept_snapshot(link):
        levels = []
        concept = link.concept
        while concept is not None:
            levels.append(
                {
                    "concept_id": concept.pk,
                    "name": concept.name,
                    "depth": concept.depth,
                }
            )
            concept = concept.parent
        levels.reverse()
        return {
            "concept_id": link.concept_id,
            "name": link.concept.name,
            "path": link.concept.full_path,
            "is_primary": link.is_primary,
            "levels": levels,
        }

    frozen_concepts = [concept_snapshot(link) for link in concept_links]
    frozen_primary = next(
        (concept for concept in frozen_concepts if concept["is_primary"]),
        None,
    )
    mapping_projection = project_question_kc_mapping(question)
    snapshot = QuestionSnapshot.objects.create(
        version=version,
        source_question=question,
        source_sequence=question.sequence,
        course_sequence=(
            question.course_identity.course_sequence
            if getattr(question, "course_identity", None) is not None
            else None
        ),
        stable_code=(
            question.course_identity.stable_code
            if getattr(question, "course_identity", None) is not None
            else ""
        ),
        source_bank_id=question.bank_id,
        source_bank_name=question.bank.name,
        bank_sequence=question.sequence,
        code=question.code,
        question_type=question.question_type,
        text=question.text,
        options=[
            {"key": option.key, "text": option.text}
            for option in question.options.all().order_by("pk")
        ],
        correct_answer=question.correct_answer,
        accepted_answers=question.accepted_answers,
        absolute_tolerance=question.absolute_tolerance,
        relative_tolerance=question.relative_tolerance,
        explanation=question.explanation,
        teacher_difficulty=question.teacher_difficulty,
        empirical_difficulty=question.empirical_difficulty,
        primary_kc=primary,
        all_kcs=[link.concept.name for link in concept_links],
        primary_kc_id_snapshot=(primary_link.concept_id if primary_link else None),
        primary_kc_path=(primary_link.concept.full_path if primary_link else ""),
        primary_kc_levels=(frozen_primary["levels"] if frozen_primary else []),
        all_kc_paths=[link.concept.full_path for link in concept_links],
        all_kc_snapshots=frozen_concepts,
        root_kc_code=mapping_projection["root_kc_code"] or "",
        kc_mapping_snapshot=mapping_projection,
        kc_mapping_schema_version=mapping_projection["schema_version"],
        kc_mapping_hash=mapping_projection["mapping_hash"],
        release_fingerprint=question_release_fingerprint(question),
    )
    return snapshot


def _create_first_release(experiment, actor, candidates, definition_fingerprint):
    version_number = (
        experiment.versions.order_by("-version_number")
        .values_list("version_number", flat=True)
        .first()
        or 0
    ) + 1
    version = ExperimentVersion.objects.create(
        experiment=experiment,
        version_number=version_number,
        configuration=_release_configuration(experiment, candidates),
        course_name_snapshot=experiment.course.name if experiment.course_id else "",
        course_code_snapshot=experiment.course.code if experiment.course_id else "",
        course_term_snapshot=experiment.course.term if experiment.course_id else "",
        assignment_unit_type=experiment.assignment_unit_type,
        published_by=actor,
        timer_semantics_version="continuous_wall_clock_v2",
        randomization_semantics_version="task_root_hmac_v2",
        definition_fingerprint=definition_fingerprint,
        canonicalization_version="canonical-json-v1",
    )
    for question in candidates:
        snapshot = _create_question_snapshot(version, question)
        ReleaseSnapshotQuestionMapping.objects.create(
            release_snapshot=version,
            source_question=question,
            question_snapshot=snapshot,
            question_release_fingerprint=snapshot.release_fingerprint,
        )
    experiment.status = Experiment.Status.FROZEN
    experiment.frozen_release_snapshot = version
    experiment.save(update_fields=["status", "frozen_release_snapshot", "updated_at"])
    return version


def _assert_preview_release_mapping(preview_run, release_snapshot):
    mappings = {
        mapping.source_question_id: mapping
        for mapping in ReleaseSnapshotQuestionMapping.objects.select_for_update()
        .filter(release_snapshot=release_snapshot)
        .select_related("question_snapshot")
    }
    items = PublishPreviewItem.objects.select_for_update().filter(
        participant__class_result__preview_run=preview_run
    )
    for item in items:
        mapping = mappings.get(item.source_question_id)
        if mapping is None or mapping.question_release_fingerprint != item.question_release_fingerprint:
            raise PublicationConflict(
                "预览题目与冻结发布快照不匹配。",
                code="PREVIEW_RELEASE_MISMATCH",
            )
        item.release_mapping = mapping
        item.save(update_fields=["release_mapping"])
    return mappings


def _assert_preview_is_current(plan, preview_run, experiment):
    if preview_run.status != PublishPreviewRun.Status.VALID:
        raise PublicationConflict("随机化预览已失效，请重新运行。", code="PREVIEW_INVALIDATED")
    if preview_run.plan_revision != plan.plan_revision:
        raise PublicationConflict("发布计划已变化，请重新运行随机化预览。", code="PREVIEW_INVALIDATED")
    candidates = _preview_candidates(experiment)
    definition_fingerprint = _preview_definition_fingerprint(experiment, candidates)
    if definition_fingerprint != preview_run.definition_fingerprint:
        preview_run.status = PublishPreviewRun.Status.INVALIDATED
        preview_run.invalidated_at = database_now()
        preview_run.invalidation_reason = "definition_changed"
        preview_run.save(update_fields=["status", "invalidated_at", "invalidation_reason"])
        PublishPreviewInvalidation.objects.create(
            preview_run=preview_run,
            reason="definition_changed",
        )
        raise PublicationConflict("实验定义或候选题池已变化，请重新运行随机化预览。", code="PREVIEW_INVALIDATED")
    return candidates


def _create_task_from_preview(class_result, release_snapshot, actor, now):
    plan_class = class_result.publish_plan_class
    class_group = plan_class.class_group
    snapshot = ExperimentClassSnapshot.objects.create(
        version=release_snapshot,
        source_class_group=class_group,
        name_snapshot=class_group.name,
        academic_year_snapshot=class_group.academic_year,
        capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
    )
    with _class_experiment_task_publication_scope():
        task = ClassExperimentTask.objects.create(
            class_group=class_group,
            experiment=release_snapshot.experiment,
            release_snapshot=release_snapshot,
            class_snapshot=snapshot,
            published_at=release_snapshot.published_at,
            class_published_at=now,
            published_by=actor,
            randomization_identity=class_result.randomization_identity,
            preview_class_result=class_result,
        )
    ScheduleRevision.objects.create(
        class_task=task,
        revision_no=1,
        opens_at=effective_plan_schedule(plan_class)[0],
        closes_at=effective_plan_schedule(plan_class)[1],
        created_by=actor,
    )
    participants = (
        class_result.participants.select_for_update(of=("self",))
        .select_related("student", "course_subject")
        .order_by("student_id")
    )
    release_configuration = release_snapshot.configuration
    for participant in participants:
        assignment_snapshot = [
            {
                "assigned_sequence_index": item.assigned_sequence_index,
                "question_snapshot_id": item.release_mapping.question_snapshot_id,
                "source_question_id": item.source_question_id,
                "sampling_source": (
                    item.sampling_source
                    if item.sampling_source
                    else {
                        "mode": (
                            "type_quota_legacy"
                            if release_configuration.get("selection_mode")
                            == Experiment.SelectionMode.QUOTA
                            else "all_legacy"
                        ),
                        "inclusion_reason": "legacy_selection",
                        "matched_nodes": [],
                        "quota_kc_code": None,
                    }
                ),
            }
            for item in participant.items.select_related(
                "release_mapping__question_snapshot"
            ).order_by("assigned_sequence_index")
        ]
        ExperimentStudent.objects.get_or_create(
            experiment=release_snapshot.experiment,
            student=participant.student,
            defaults={"class_snapshot_name": snapshot.display_name},
        )
        ExperimentVersionParticipantSnapshot.objects.create(
            version=release_snapshot,
            student=participant.student,
            class_snapshot=snapshot,
            class_task=task,
            course_subject=participant.course_subject,
            randomization_subject_key=participant.randomization_subject_key,
            sampling_seed=participant.sampling_seed,
            ordering_seed=participant.ordering_seed,
            sampling_algorithm_version=participant.sampling_algorithm_version,
            assignment_snapshot=assignment_snapshot,
        )
    return task


@transaction.atomic
def commit_publish_plan(plan, actor, *, expected_revision):
    """Atomically freeze once and create every selected class task or none."""
    plan_identity = PublishPlan.objects.values(
        "experiment_id", "experiment__course_id"
    ).get(pk=plan.pk)
    course_id = plan_identity["experiment__course_id"]
    if course_id is not None:
        Course.objects.select_for_update().get(pk=course_id)
    experiment = Experiment.objects.select_for_update().get(
        pk=plan_identity["experiment_id"]
    )
    plan = PublishPlan.objects.select_for_update().get(pk=plan.pk)
    now = database_now()
    if plan.status != PublishPlan.Status.PREVIEWED or plan.preview_run_id is None:
        raise PublicationConflict("请先完成有效的随机化预览。", code="PREVIEW_INVALIDATED")
    if plan.expires_at <= now:
        plan.status = PublishPlan.Status.EXPIRED
        plan.save(update_fields=["status"])
        raise PublicationConflict("发布计划已过期。", code="PUBLISH_PLAN_EXPIRED")
    _assert_plan_editor(plan, actor)
    if plan.plan_revision != expected_revision:
        raise PublicationConflict("发布计划已被更新，请刷新后重试。", code="PUBLISH_PLAN_STALE")
    if experiment.status == Experiment.Status.ARCHIVED:
        raise PublicationConflict("归档实验不能继续发布班级。", code="EXPERIMENT_ARCHIVED")
    preview_run = PublishPreviewRun.objects.select_for_update().get(pk=plan.preview_run_id)
    candidates = _assert_preview_is_current(plan, preview_run, experiment)
    if experiment.status == Experiment.Status.DRAFT:
        release_snapshot = _create_first_release(
            experiment,
            actor,
            candidates,
            preview_run.definition_fingerprint,
        )
    else:
        release_snapshot = experiment.frozen_release_snapshot
        if release_snapshot is None:
            raise PublicationConflict("冻结实验缺少正式发布快照。", code="PREVIEW_RELEASE_MISMATCH")
    _assert_preview_release_mapping(preview_run, release_snapshot)
    class_results = list(
        PublishPreviewClassResult.objects.select_for_update(of=("self",))
        .filter(preview_run=preview_run)
        .select_related("publish_plan_class__class_group", "randomization_identity")
        .order_by("publish_plan_class__class_group_id")
    )
    if not class_results:
        raise PublicationConflict("预览中没有可发布班级。", code="PREVIEW_INVALIDATED")
    for result in class_results:
        if ClassExperimentTask.objects.select_for_update().filter(
            experiment=experiment,
            class_group_id=result.publish_plan_class.class_group_id,
        ).exists():
            raise PublicationConflict("该班级已存在本实验的正式任务。", code="TASK_ALREADY_EXISTS")
        _assert_participants_not_formally_assigned(
            experiment,
            list(result.participants.values_list("student", flat=True)),
        )
    tasks = [
        _create_task_from_preview(result, release_snapshot, actor, now)
        for result in class_results
    ]
    plan.status = PublishPlan.Status.COMMITTED
    plan.committed_at = now
    plan.current_step = 2
    plan.save(update_fields=["status", "committed_at", "current_step"])
    return tasks


@transaction.atomic
def append_schedule_revision(task, actor, *, opens_at, closes_at, reason):
    task = ClassExperimentTask.objects.select_for_update().get(pk=task.pk)
    now = database_now()
    current = task.schedule_revisions.select_for_update().order_by("-revision_no").first()
    if task.cancelled_at or task.manual_closed_at or task.invalidated_at:
        raise PublicationConflict("已终止任务不能修改开放排期。", code="SCHEDULE_LOCKED")
    if current is None or current.opens_at is None or now >= current.opens_at:
        raise PublicationConflict("仅在开放前允许修正排期。", code="SCHEDULE_LOCKED")
    if task.sessions.exists():
        raise PublicationConflict("已有学生会话，不能修改开放排期。", code="SCHEDULE_LOCKED")
    if closes_at and opens_at and closes_at <= opens_at:
        raise ValidationError("截止时间必须晚于开始时间。")
    return ScheduleRevision.objects.create(
        class_task=task,
        revision_no=current.revision_no + 1,
        opens_at=opens_at,
        closes_at=closes_at,
        created_by=actor,
        reason=str(reason or "").strip(),
    )


@transaction.atomic
def cancel_task(task, actor, *, reason):
    task = ClassExperimentTask.objects.select_for_update().get(pk=task.pk)
    now = database_now()
    schedule = task.schedule_revisions.order_by("-revision_no").first()
    if task.sessions.exists() or schedule is None or schedule.opens_at is None or now >= schedule.opens_at:
        raise PublicationConflict("仅在开放前且尚无会话时允许取消任务。", code="TASK_TERMINATED")
    if task.cancelled_at:
        return task
    task.cancelled_at = now
    task.terminal_reason = str(reason or "cancelled").strip() or "cancelled"
    with _class_experiment_task_terminal_update_scope():
        task.save(update_fields=["cancelled_at", "terminal_reason"])
    return task


@transaction.atomic
def manual_close_task(task, actor, *, reason):
    """The Task lock makes manual close win over later waiting submissions."""
    task = ClassExperimentTask.objects.select_for_update().get(pk=task.pk)
    now = database_now()
    if task.cancelled_at or task.manual_closed_at or task.invalidated_at:
        return task
    task.manual_closed_at = now
    task.terminal_reason = str(reason or "manual_closed").strip() or "manual_closed"
    with _class_experiment_task_terminal_update_scope():
        task.save(update_fields=["manual_closed_at", "terminal_reason"])
    from apps.assessment.models import Attempt, ExperimentSession
    from apps.assessment.services import _append_session_event, _close_open_attempts

    sessions = list(
        ExperimentSession.objects.select_for_update().filter(
            class_task=task,
            status=ExperimentSession.Status.IN_PROGRESS,
        )
    )
    for session in sessions:
        _close_open_attempts(
            session,
            status=Attempt.Status.UNANSWERED,
            close_reason="manual_closed",
            event_close_reason="unanswered",
            authoritative_terminal_at=now,
        )
        session.status = ExperimentSession.Status.EXPIRED
        session.completed_at = now
        session.terminal_at = now
        session.terminal_reason = "manual_closed"
        session.authoritative_terminal_at = now
        session.terminal_recorded_at = timezone.now()
        session.save(
            update_fields=[
                "status",
                "completed_at",
                "terminal_at",
                "terminal_reason",
                "authoritative_terminal_at",
                "terminal_recorded_at",
            ]
        )
        _append_session_event(
            session,
            event_type="session_expired",
            payload={"expiration_reason": "manual_closed", "terminal_at": now.isoformat()},
        )
    return task
