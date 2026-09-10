import hashlib
import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from django.db.models import Q

from .confidence_scales import CONFIDENCE_SCALE_CHOICES


def canonical_presentation_hash(
    *,
    presentation_variant,
    option_order,
    parameter_values,
    random_seed,
):
    canonical = json.dumps(
        {
            "option_order": option_order,
            "parameter_values": parameter_values,
            "presentation_variant": presentation_variant,
            "random_seed": random_seed,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def presentation_option_keys(question_snapshot):
    if question_snapshot.question_type == "true_false":
        return ["true", "false"]
    return [option["key"] for option in question_snapshot.options]

class ExperimentQuerySet(models.QuerySet):
    _course_field_names = frozenset({"course", "course_id"})

    def _writes_course(self, values):
        return bool(self._course_field_names.intersection(values))

    def _writes_frozen_definition(self, values):
        normalized = {
            f"{field_name}_id"
            if field_name in {"course", "question_bank", "frozen_release_snapshot"}
            else field_name
            for field_name in values
        }
        return bool(normalized.intersection(Experiment.FROZEN_DEFINITION_FIELDS))

    def _locked_course_write_target_ids(self):
        return list(
            self.select_for_update()
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    def update(self, **kwargs):
        writes_course = self._writes_course(kwargs)
        writes_frozen_definition = self._writes_frozen_definition(kwargs)
        rolls_back_release = kwargs.get("status") == Experiment.Status.DRAFT
        if not writes_course and not writes_frozen_definition and not rolls_back_release:
            return super().update(**kwargs)
        self._not_support_combined_queries("update")
        if self.query.is_sliced:
            raise TypeError("Cannot update a query once a slice has been taken.")
        self._for_write = True
        using = self.db
        locked_scope = self.using(using)
        with transaction.atomic(using=using):
            target_ids = locked_scope._locked_course_write_target_ids()
            if not target_ids:
                self._result_cache = None
                return 0
            locked_targets = locked_scope.filter(pk__in=target_ids)
            if (
                writes_frozen_definition
                and locked_targets.filter(
                    frozen_release_snapshot__isnull=False
                ).exists()
            ):
                raise ValidationError("实验定义已冻结，不能批量修改。")
            if (
                rolls_back_release
                and locked_targets.filter(
                    frozen_release_snapshot__isnull=False
                ).exists()
            ):
                raise ValidationError("已冻结实验不能退回草稿状态。")
            if writes_course and locked_targets.filter(class_tasks__isnull=False).exists():
                raise ValidationError("已存在正式班级实验任务，实验所属课程不可变更。")
            # Update only the rows whose original queryset scope was locked.
            rows = models.QuerySet.update(locked_targets, **kwargs)
            self._result_cache = None
            return rows

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        update_fields = tuple(update_fields) if update_fields is not None else None
        # Non-course conflict upserts remain a public write surface; course
        # field upserts are rejected. No current application caller uses it.
        if update_conflicts and self._writes_course(update_fields or ()):
            raise ValidationError("实验不支持批量冲突更新。")
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )


class ExperimentManager(models.Manager.from_queryset(ExperimentQuerySet)):
    pass


class Experiment(models.Model):
    FROZEN_DEFINITION_FIELDS = (
        "name",
        "description",
        "course_id",
        "question_bank_id",
        "selection_mode",
        "sampling_mode",
        "sampling_total_count",
        "sampling_match_scope",
        "ordering_mode",
        "feedback_mode",
        "feedback_release_policy",
        "research_tools",
        "time_mode",
        "total_time_limit_minutes",
        "per_question_time_limit_seconds",
        "resume_mode",
        "confidence_mode",
        "confidence_sampling_unit",
        "confidence_probability",
        "confidence_randomization_seed",
        "confidence_assignment_version",
        "confidence_scale_version",
        "confidence_prompt_version",
        "assignment_unit_type",
        "heartbeat_interval_seconds",
        "frozen_release_snapshot_id",
    )
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        FROZEN = "frozen", "已冻结"
        PUBLISHED = "published", "已发布"
        CLOSED = "closed", "已关闭"
        ARCHIVED = "archived", "已归档"
    class Ordering(models.TextChoices):
        FIXED = "fixed", "固定顺序"
        RANDOM = "random", "随机顺序"
        STRATIFIED = "stratified_random", "按 KC 与难度分层随机"
    class SelectionMode(models.TextChoices):
        ALL = "all", "全量题池"
        QUOTA = "quota", "按题型配额抽题"
    class SamplingMode(models.TextChoices):
        TOTAL_COUNT = "total_count", "按总题数抽题"
        KC_QUOTA = "kc_quota", "按知识点配额抽题"
    class MatchScope(models.TextChoices):
        PRIMARY = "primary", "主 KC"
        ANY_LEVEL = "any_level", "任意层级"
    class Feedback(models.TextChoices):
        NONE = "none", "仅显示已提交"
        CORRECTNESS = "correctness", "显示正误"
        FULL = "full", "显示正误、答案与解析"
    class FeedbackReleasePolicy(models.TextChoices):
        AFTER_PERSONAL_SUBMISSION = "after_personal_submission", "学生完成后"
        AFTER_ASSIGNMENT_DEADLINE = "after_assignment_deadline", "班级任务截止后"
        MANUAL_RELEASE = "manual_release", "教师人工发布"
    class TimeMode(models.TextChoices):
        NONE = "none", "无时限"
        TOTAL = "total", "实验总时限"
        PER_QUESTION = "per_question", "单题时限"
    class ResumeMode(models.TextChoices):
        SINGLE = "single_session", "必须一次完成"
        RESUMABLE = "resumable", "允许断点续答"
    class ConfidenceMode(models.TextChoices):
        OFF = "off", "不采集"
        ALL = "all", "全部采集"
        SAMPLED = "sampled", "抽样采集"
    class ConfidenceSamplingUnit(models.TextChoices):
        STUDENT = "student", "学生"
        SESSION = "session", "会话"
        ATTEMPT = "attempt", "作答"
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    course = models.ForeignKey(
        "accounts.Course",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="experiments",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_experiments",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="experiments",
    )
    question_bank = models.ForeignKey("question_bank.QuestionBank", null=True, blank=True, on_delete=models.PROTECT, related_name="experiments")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    frozen_release_snapshot = models.ForeignKey(
        "ExperimentVersion",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="frozen_for_experiments",
    )
    selection_mode = models.CharField(
        max_length=16,
        choices=SelectionMode.choices,
        default=SelectionMode.ALL,
    )
    sampling_mode = models.CharField(
        max_length=20,
        choices=SamplingMode.choices,
        null=True,
        blank=True,
    )
    sampling_total_count = models.PositiveIntegerField(null=True, blank=True)
    sampling_match_scope = models.CharField(
        max_length=16,
        choices=MatchScope.choices,
        default=MatchScope.ANY_LEVEL,
    )
    ordering_mode = models.CharField(max_length=24, choices=Ordering.choices, default=Ordering.FIXED)
    feedback_mode = models.CharField(max_length=16, choices=Feedback.choices, default=Feedback.NONE)
    feedback_release_policy = models.CharField(
        max_length=32,
        choices=FeedbackReleasePolicy.choices,
        default=FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
    )
    research_tools = models.JSONField(default=dict, blank=True)
    time_mode = models.CharField(max_length=20, choices=TimeMode.choices, default=TimeMode.NONE)
    total_time_limit_minutes = models.PositiveIntegerField(null=True, blank=True)
    per_question_time_limit_seconds = models.PositiveIntegerField(null=True, blank=True)
    resume_mode = models.CharField(max_length=20, choices=ResumeMode.choices, default=ResumeMode.RESUMABLE)
    confidence_mode = models.CharField(
        max_length=12,
        choices=ConfidenceMode.choices,
        default=ConfidenceMode.OFF,
    )
    confidence_sampling_unit = models.CharField(
        max_length=12,
        choices=ConfidenceSamplingUnit.choices,
        default=ConfidenceSamplingUnit.ATTEMPT,
    )
    confidence_probability = models.DecimalField(
        max_digits=4,
        decimal_places=3,
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(1)],
    )
    confidence_randomization_seed = models.CharField(max_length=128, blank=True, default="")
    confidence_assignment_version = models.CharField(max_length=64, default="v1")
    confidence_scale_version = models.CharField(
        max_length=64,
        choices=CONFIDENCE_SCALE_CHOICES,
        default="v2",
    )
    confidence_prompt_version = models.CharField(max_length=64, default="v1")
    assignment_unit_type = models.CharField(max_length=16, choices=(("subject", "Subject"), ("session", "Session")), default="subject")
    heartbeat_interval_seconds = models.PositiveSmallIntegerField(
        default=30,
        validators=[MinValueValidator(15), MaxValueValidator(120)],
    )
    questions = models.ManyToManyField("question_bank.Question", through="ExperimentQuestion", related_name="experiments")
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, through="ExperimentStudent", related_name="assigned_experiments")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    objects = ExperimentManager()

    class Meta:
        ordering = ["-updated_at"]
        constraints = [
            models.CheckConstraint(
                condition=Q(
                    confidence_probability__isnull=True,
                )
                | Q(
                    confidence_probability__gte=0,
                    confidence_probability__lte=1,
                ),
                name="experiment_confidence_probability_range",
            ),
            models.CheckConstraint(
                condition=Q(
                    heartbeat_interval_seconds__gte=15,
                    heartbeat_interval_seconds__lte=120,
                ),
                name="experiment_heartbeat_interval_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        confidence_mode="sampled",
                        confidence_probability__isnull=False,
                    )
                    | (
                        ~Q(confidence_mode="sampled")
                        & Q(confidence_probability__isnull=True)
                    )
                ),
                name="experiment_confidence_mode_probability",
            ),
        ]
        base_manager_name = "objects"
        default_manager_name = "objects"

    def __str__(self): return self.name

    @property
    def is_definition_frozen(self):
        return self.status in {
            self.Status.FROZEN,
            self.Status.PUBLISHED,
            self.Status.CLOSED,
            self.Status.ARCHIVED,
        }

    def clean(self):
        super().clean()
        errors = {}
        if not self.question_bank_id and self.status != self.Status.DRAFT:
            errors["question_bank"] = "正式实验必须关联题库。"
        if self.course_id and self.question_bank_id and self.question_bank.course_id != self.course_id:
            errors["question_bank"] = "实验只能使用同一课程的题库。"
        if self.confidence_mode == self.ConfidenceMode.SAMPLED:
            if self.confidence_probability is None:
                errors["confidence_probability"] = "抽样采集必须设置抽样概率。"
        elif self.confidence_probability is not None:
            errors["confidence_probability"] = "仅抽样采集模式可以设置抽样概率。"
        if errors:
            raise ValidationError(errors)

    def _task_backed_course_is_changing(self, update_fields, *, using):
        if self._state.adding:
            return False
        if update_fields is not None and not {"course", "course_id"}.intersection(
            update_fields
        ):
            return False
        current_course_id = self.__class__._base_manager.using(using).filter(
            pk=self.pk
        ).values_list("course_id", flat=True).get()
        return current_course_id != self.course_id

    def _save_with_owner_defaults(self, *args, **kwargs):
        created = self._state.adding
        if self.created_by_id is None and self.owner_id is not None:
            self.created_by_id = self.owner_id
        if self.owner_id is None and self.created_by_id is not None:
            self.owner_id = self.created_by_id
        super().save(*args, **kwargs)
        if created:
            ExperimentQuestionQuota.objects.bulk_create(
                [
                    ExperimentQuestionQuota(experiment=self, question_type=question_type, amount=0)
                    for question_type, _label in QuestionSnapshot._meta.get_field("question_type").choices
                ],
                ignore_conflicts=True,
            )

    def _frozen_definition_is_changing(self, update_fields, *, using):
        if self._state.adding:
            return False
        normalized_update_fields = {
            f"{field_name}_id" if field_name in {"course", "question_bank", "frozen_release_snapshot"} else field_name
            for field_name in (update_fields or ())
        }
        rolls_back_release = self.status == self.Status.DRAFT and (
            update_fields is None or "status" in normalized_update_fields
        )
        if (
            update_fields is not None
            and not normalized_update_fields.intersection(
                self.FROZEN_DEFINITION_FIELDS
            )
            and not rolls_back_release
        ):
            return False
        current = (
            self.__class__._base_manager.using(using)
            .filter(pk=self.pk)
            .values("status", *self.FROZEN_DEFINITION_FIELDS)
            .get()
        )
        if not current["frozen_release_snapshot_id"]:
            return False
        if rolls_back_release:
            raise ValidationError("已冻结实验不能退回草稿状态。")
        return any(
            current[field_name] != getattr(self, field_name)
            for field_name in self.FROZEN_DEFINITION_FIELDS
        )

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        save_kwargs = {**kwargs, "using": using}
        if self._frozen_definition_is_changing(
            save_kwargs.get("update_fields"),
            using=using,
        ):
            raise ValidationError("实验定义已冻结，不能再修改。")
        if not self._task_backed_course_is_changing(
            save_kwargs.get("update_fields"),
            using=using,
        ):
            return self._save_with_owner_defaults(*args, **save_kwargs)
        with transaction.atomic(using=using):
            locked = (
                self.__class__._base_manager.using(using)
                .filter(pk=self.pk)
                .select_for_update()
                .get()
            )
            if locked.course_id != self.course_id and locked.class_tasks.exists():
                raise ValidationError("已存在正式班级实验任务，实验所属课程不可变更。")
            return self._save_with_owner_defaults(*args, **save_kwargs)

class ExperimentQuestion(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="question_links")
    question = models.ForeignKey("question_bank.Question", on_delete=models.PROTECT)
    display_order = models.PositiveIntegerField()
    class Meta:
        ordering = ["display_order"]
        constraints = [
            models.UniqueConstraint(fields=["experiment", "question"], name="unique_experiment_question"),
            models.UniqueConstraint(fields=["experiment", "display_order"], name="unique_experiment_display_order"),
        ]


class ExperimentQuestionQuota(models.Model):
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="question_quotas",
    )
    question_type = models.CharField(
        max_length=24,
        choices=[
            ("single_choice", "单选题"),
            ("multiple_choice", "多选题"),
            ("numeric", "数值题"),
            ("short_answer", "简答题"),
            ("fill_blank", "填空题"),
            ("true_false", "判断题"),
        ],
    )
    amount = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["question_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "question_type"],
                name="unique_experiment_question_type_quota",
            ),
            models.CheckConstraint(
                condition=Q(amount__gte=0),
                name="experiment_question_quota_nonnegative",
            ),
        ]


class ExperimentSamplingKC(models.Model):
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="sampling_kcs",
    )
    concept = models.ForeignKey(
        "question_bank.Concept",
        on_delete=models.PROTECT,
        related_name="experiment_sampling_rules",
    )
    quota = models.PositiveIntegerField(null=True, blank=True)
    match_scope = models.CharField(
        max_length=16,
        choices=Experiment.MatchScope.choices,
        default=Experiment.MatchScope.PRIMARY,
    )

    class Meta:
        ordering = ["concept_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "concept"],
                name="unique_experiment_sampling_kc",
            ),
            models.CheckConstraint(
                condition=Q(quota__isnull=True) | Q(quota__gt=0),
                name="experiment_sampling_kc_positive_quota",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.experiment_id
            and self.concept_id
            and self.experiment.course_id != self.concept.course_id
        ):
            raise ValidationError({"concept": "抽题知识点必须属于实验课程。"})
        if self.concept_id and self.concept.status != self.concept.Status.ACTIVE:
            raise ValidationError({"concept": "归档知识点不能用于新的实验规则。"})

    def save(self, *args, **kwargs):
        self.clean()
        return super().save(*args, **kwargs)


class ExperimentStudent(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="student_links")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="experiment_links")
    class_snapshot_name = models.CharField(max_length=160, blank=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["experiment", "student"], name="unique_experiment_student")]

    def clean(self):
        super().clean()
        if not self.experiment_id or not self.student_id or self.experiment.course_id is None:
            return
        from apps.accounts.models import CourseEnrollment

        if not CourseEnrollment.objects.filter(
            course_id=self.experiment.course_id,
            student_id=self.student_id,
            is_active=True,
        ).exists():
            raise ValidationError({"student": "学生必须先加入实验所属课程。"})

class ExperimentParticipantClass(models.Model):
    """A draft rule that includes every currently active student in one class."""

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="participant_class_links",
    )
    class_group = models.ForeignKey(
        "accounts.ClassGroup",
        on_delete=models.PROTECT,
        related_name="experiment_participant_class_links",
    )

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "class_group"],
                name="unique_experiment_participant_class",
            )
        ]

    def clean(self):
        super().clean()
        if not self.experiment_id or not self.class_group_id:
            return
        if self.experiment.course_id is None:
            raise ValidationError({"experiment": "参与班级仅适用于课程实验。"})
        if self.experiment.status != Experiment.Status.DRAFT:
            raise ValidationError({"experiment": "发布后不能修改参与规则。"})
        from apps.accounts.models import Course

        if self.experiment.course.status != Course.Status.ACTIVE:
            raise ValidationError({"experiment": "课程已归档，不能修改参与规则。"})
        if self.class_group.course_id != self.experiment.course_id:
            raise ValidationError({"class_group": "参与班级必须属于实验所属课程。"})
        if not self.class_group.is_active:
            raise ValidationError({"class_group": "不能选择已停用的班级。"})


class ExperimentParticipantOverride(models.Model):
    """An explicit inclusion or exclusion layered over participant classes."""

    class Mode(models.TextChoices):
        INCLUDE = "include", "显式纳入"
        EXCLUDE = "exclude", "从班级中排除"

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="participant_override_links",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="experiment_participant_override_links",
    )
    mode = models.CharField(max_length=12, choices=Mode.choices)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "student"],
                name="unique_experiment_participant_override",
            ),
            models.CheckConstraint(
                condition=Q(mode__in=["include", "exclude"]),
                name="experiment_participant_override_mode",
            ),
        ]

    def clean(self):
        super().clean()
        if not self.experiment_id or not self.student_id:
            return
        if self.experiment.course_id is None:
            raise ValidationError({"experiment": "参与学生规则仅适用于课程实验。"})
        if self.experiment.status != Experiment.Status.DRAFT:
            raise ValidationError({"experiment": "发布后不能修改参与规则。"})
        from apps.accounts.models import Course, CourseEnrollment

        if self.experiment.course.status != Course.Status.ACTIVE:
            raise ValidationError({"experiment": "课程已归档，不能修改参与规则。"})
        if not CourseEnrollment.objects.filter(
            course_id=self.experiment.course_id,
            student_id=self.student_id,
            is_active=True,
            class_group__is_active=True,
            student__role="student",
            student__is_active=True,
        ).exists():
            raise ValidationError({"student": "学生必须是当前课程可用班级中的在读学生。"})


class ExperimentVersionQuerySet(models.QuerySet):
    def _assert_not_task_backed(self):
        if self.filter(class_tasks__isnull=False).exists():
            raise ValidationError("已用于班级实验任务的发布快照不可修改或删除。")

    def update(self, **kwargs):
        self._assert_not_task_backed()
        return super().update(**kwargs)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        # Release facts are created by publication or migrations, never by a
        # public conflict-upsert path.
        if update_conflicts:
            raise ValidationError("实验发布快照不支持批量冲突更新。")
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def delete(self):
        self._assert_not_task_backed()
        return super().delete()


class ExperimentVersionManager(models.Manager.from_queryset(ExperimentVersionQuerySet)):
    pass


class ExperimentVersion(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    configuration = models.JSONField(default=dict)
    course_name_snapshot = models.CharField(max_length=160, blank=True)
    course_code_snapshot = models.CharField(max_length=64, blank=True)
    course_term_snapshot = models.CharField(max_length=64, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)
    assignment_unit_type = models.CharField(max_length=16, choices=(("subject", "Subject"), ("session", "Session")), default="subject")
    timer_semantics_version = models.CharField(
        max_length=64,
        default="continuous_wall_clock_v2",
    )
    randomization_semantics_version = models.CharField(
        max_length=64,
        default="task_root_hmac_v2",
    )
    definition_fingerprint = models.CharField(max_length=64, blank=True, default="")
    canonicalization_version = models.CharField(max_length=32, default="canonical-json-v1")
    objects = ExperimentVersionManager()

    class Meta:
        constraints = [models.UniqueConstraint(fields=["experiment", "version_number"], name="unique_experiment_version")]
        base_manager_name = "objects"
        default_manager_name = "objects"

    def _is_task_backed(self):
        return self.pk is not None and self.class_tasks.exists()

    def save(self, *args, **kwargs):
        if not self._state.adding and self._is_task_backed():
            raise ValidationError("已用于班级实验任务的发布快照不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_task_backed():
            raise ValidationError("已用于班级实验任务的发布快照不可删除。")
        return super().delete(*args, **kwargs)

    def __str__(self): return f"{self.experiment} v{self.version_number}"


class ClassExperimentTaskQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("班级实验任务不可批量修改。")

    def bulk_update(self, objs, fields, batch_size=None):
        raise ValidationError("班级实验任务不可批量修改。")

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        raise ValidationError("班级实验任务只能在实验发布事务中创建。")

    def delete(self):
        raise ValidationError("班级实验任务不可删除。")


class ClassExperimentTaskManager(
    models.Manager.from_queryset(ClassExperimentTaskQuerySet)
):
    pass


class ClassExperimentTask(models.Model):
    """One permanent formal experiment task for a class and experiment pair."""

    class_group = models.ForeignKey(
        "accounts.ClassGroup",
        on_delete=models.PROTECT,
        related_name="experiment_tasks",
    )
    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.PROTECT,
        related_name="class_tasks",
    )
    release_snapshot = models.ForeignKey(
        ExperimentVersion,
        on_delete=models.PROTECT,
        related_name="class_tasks",
    )
    class_snapshot = models.OneToOneField(
        "ExperimentClassSnapshot",
        on_delete=models.PROTECT,
        related_name="class_task",
    )
    published_at = models.DateTimeField()
    class_published_at = models.DateTimeField(null=True, blank=True)
    published_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="published_class_experiment_tasks",
    )
    randomization_identity = models.ForeignKey(
        "ExperimentClassRandomizationIdentity",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="class_tasks",
    )
    preview_class_result = models.ForeignKey(
        "PublishPreviewClassResult",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="published_tasks",
    )
    cancelled_at = models.DateTimeField(null=True, blank=True)
    manual_closed_at = models.DateTimeField(null=True, blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    feedback_released_at = models.DateTimeField(null=True, blank=True)
    feedback_released_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="released_feedback_class_experiment_tasks",
    )
    terminal_reason = models.CharField(max_length=64, blank=True, default="")
    provenance = models.CharField(max_length=64, default="native")
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ClassExperimentTaskManager()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["class_group", "experiment"],
                name="unique_class_experiment_task",
            ),
        ]
        indexes = [
            models.Index(
                fields=["class_group", "-published_at"],
                name="class_task_class_pub_idx",
            ),
        ]
        base_manager_name = "objects"
        default_manager_name = "objects"

    def clean(self):
        super().clean()
        errors = {}
        if (
            self.class_group_id
            and self.experiment_id
            and self.class_group.course_id != self.experiment.course_id
        ):
            errors["class_group"] = "班级必须属于实验所属课程。"
        if (
            self.release_snapshot_id
            and self.experiment_id
            and self.release_snapshot.experiment_id != self.experiment_id
        ):
            errors["release_snapshot"] = "发布快照必须属于同一实验。"
        if (
            self.class_snapshot_id
            and self.release_snapshot_id
            and self.class_snapshot.version_id != self.release_snapshot_id
        ):
            errors["class_snapshot"] = "班级快照必须属于发布快照。"
        if (
            self.class_snapshot_id
            and self.class_group_id
            and self.class_snapshot.source_class_group_id != self.class_group_id
        ):
            errors["class_snapshot"] = "班级快照必须对应同一班级。"
        if (
            self.published_at is not None
            and self.release_snapshot_id
            and self.published_at != self.release_snapshot.published_at
        ):
            errors["published_at"] = "任务发布时间必须与发布快照一致。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            previous = type(self).objects.get(pk=self.pk)
            mutable_fields = {
                "cancelled_at",
                "manual_closed_at",
                "invalidated_at",
                "feedback_released_at",
                "feedback_released_by",
                "terminal_reason",
            }
            changed = {
                field.name
                for field in self._meta.fields
                if getattr(self, field.attname) != getattr(previous, field.attname)
            }
            from .services import _class_experiment_task_terminal_update_is_active

            if (
                not changed
                or not changed.issubset(mutable_fields)
                or not _class_experiment_task_terminal_update_is_active()
            ):
                raise ValidationError("班级实验任务的正式发布事实不可修改。")
            return super().save(*args, **kwargs)
        from .services import _class_experiment_task_creation_is_active

        if (
            not transaction.get_connection().in_atomic_block
            or not _class_experiment_task_creation_is_active()
        ):
            raise ValidationError("班级实验任务只能在实验发布事务中创建。")
        self.full_clean(validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError("班级实验任务不可删除。")


class ScheduleRevision(models.Model):
    class_task = models.ForeignKey(
        ClassExperimentTask,
        on_delete=models.CASCADE,
        related_name="schedule_revisions",
    )
    revision_no = models.PositiveIntegerField()
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    timezone_name = models.CharField(max_length=64, default="Asia/Shanghai")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_schedule_revisions",
    )
    reason = models.CharField(max_length=500, blank=True, default="")
    provenance = models.CharField(max_length=64, default="native")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["class_task_id", "revision_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_task", "revision_no"],
                name="unique_task_schedule_revision",
            ),
            models.CheckConstraint(
                condition=Q(closes_at__isnull=True)
                | Q(opens_at__isnull=True)
                | Q(closes_at__gt=models.F("opens_at")),
                name="schedule_revision_window_order",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError("开放排期历史版本不可修改。")
        return super().save(*args, **kwargs)


class ExperimentClassSnapshotQuerySet(models.QuerySet):
    def _assert_not_task_backed(self):
        if self.filter(class_task__isnull=False).exists():
            raise ValidationError("已用于班级实验任务的班级快照不可修改或删除。")

    def update(self, **kwargs):
        self._assert_not_task_backed()
        return super().update(**kwargs)

    def bulk_create(
        self,
        objs,
        batch_size=None,
        ignore_conflicts=False,
        update_conflicts=False,
        update_fields=None,
        unique_fields=None,
    ):
        # Release facts are created by publication or migrations, never by a
        # public conflict-upsert path.
        if update_conflicts:
            raise ValidationError("实验班级快照不支持批量冲突更新。")
        return super().bulk_create(
            objs,
            batch_size=batch_size,
            ignore_conflicts=ignore_conflicts,
            update_conflicts=update_conflicts,
            update_fields=update_fields,
            unique_fields=unique_fields,
        )

    def delete(self):
        self._assert_not_task_backed()
        return super().delete()


class ExperimentClassSnapshotManager(
    models.Manager.from_queryset(ExperimentClassSnapshotQuerySet)
):
    pass


class ExperimentClassSnapshot(models.Model):
    """The immutable class membership label used by one published version."""

    class CaptureMode(models.TextChoices):
        NATIVE = "native", "发布时课程班级快照"
        LEGACY_LABEL_PROJECTION = "legacy_label_projection", "历史班级标签投影"
        UNRECORDED = "unrecorded", "未记录"

    class_snapshot_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    version = models.ForeignKey(
        ExperimentVersion,
        on_delete=models.CASCADE,
        related_name="class_snapshots",
    )
    source_class_group = models.ForeignKey(
        "accounts.ClassGroup",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="experiment_class_snapshots",
    )
    name_snapshot = models.CharField(max_length=120, blank=True)
    academic_year_snapshot = models.CharField(max_length=32, blank=True)
    capture_mode = models.CharField(
        max_length=32,
        choices=CaptureMode.choices,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ExperimentClassSnapshotManager()

    class Meta:
        ordering = ["academic_year_snapshot", "name_snapshot", "class_snapshot_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "source_class_group"],
                condition=Q(source_class_group__isnull=False),
                name="unique_version_source_class_snapshot",
            ),
        ]
        base_manager_name = "objects"
        default_manager_name = "objects"

    @property
    def display_name(self):
        return f"{self.academic_year_snapshot} {self.name_snapshot}".strip()

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError("实验班级快照不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.pk and ClassExperimentTask.objects.filter(class_snapshot_id=self.pk).exists():
            raise ValidationError("已用于班级实验任务的班级快照不可删除。")
        return super().delete(*args, **kwargs)


class ExperimentVersionParticipantSnapshot(models.Model):
    """The student-to-class assignment frozen with an experiment version."""

    version = models.ForeignKey(
        ExperimentVersion,
        on_delete=models.CASCADE,
        related_name="participant_snapshots",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="experiment_version_participant_snapshots",
    )
    class_snapshot = models.ForeignKey(
        ExperimentClassSnapshot,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participant_snapshots",
    )
    class_task = models.ForeignKey(
        ClassExperimentTask,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="participant_snapshots",
    )
    course_subject = models.ForeignKey(
        "accounts.CourseSubject",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="experiment_participant_snapshots",
    )
    randomization_subject_key = models.CharField(max_length=128, blank=True, default="")
    sampling_seed = models.CharField(max_length=64, blank=True, default="")
    ordering_seed = models.CharField(max_length=64, blank=True, default="")
    sampling_algorithm_version = models.CharField(max_length=32, blank=True, default="")
    assignment_snapshot = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version_id", "student_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["class_task", "student"],
                condition=Q(class_task__isnull=False),
                name="unique_task_participant_snapshot",
            ),
        ]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError("实验参与者快照不可修改。")
        return super().save(*args, **kwargs)


class Condition(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.CASCADE, related_name="conditions")
    key = models.CharField(max_length=64)
    label = models.CharField(max_length=160, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["experiment", "key"], name="unique_experiment_condition")]


class ConditionAssignment(models.Model):
    condition = models.ForeignKey(Condition, on_delete=models.PROTECT, related_name="assignments")
    subject = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="condition_assignments")
    session = models.ForeignKey("assessment.ExperimentSession", null=True, blank=True, on_delete=models.PROTECT, related_name="condition_assignments")
    algorithm = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64)
    seed = models.CharField(max_length=256)
    draw = models.DecimalField(max_digits=20, decimal_places=10)
    stratum = models.CharField(max_length=160, blank=True, default="")
    probability = models.DecimalField(max_digits=10, decimal_places=8)
    assigned_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [
            models.CheckConstraint(condition=Q(probability__gte=0, probability__lte=1), name="condition_probability_range"),
            models.CheckConstraint(condition=Q(draw__gte=0, draw__lte=1), name="condition_draw_range"),
            models.CheckConstraint(condition=(Q(subject__isnull=False, session__isnull=True) | Q(subject__isnull=True, session__isnull=False)), name="condition_assignment_subject_or_session"),
            models.UniqueConstraint(fields=["condition", "subject"], condition=Q(subject__isnull=False), name="unique_condition_subject_assignment"),
            models.UniqueConstraint(fields=["condition", "session"], condition=Q(session__isnull=False), name="unique_condition_session_assignment"),
        ]

    def __init__(self, *args, **kwargs):
        # Accept the canonical research-contract names while retaining the
        # compact field names used by the first database migration.
        aliases = {
            "randomization_seed": "seed",
            "randomization_draw": "draw",
            "assignment_probability": "probability",
        }
        for canonical, legacy in aliases.items():
            if canonical in kwargs and legacy not in kwargs:
                kwargs[legacy] = kwargs.pop(canonical)
        super().__init__(*args, **kwargs)

    @property
    def randomization_seed(self):
        return self.seed

    @randomization_seed.setter
    def randomization_seed(self, value):
        self.seed = value

    @property
    def randomization_draw(self):
        return self.draw

    @randomization_draw.setter
    def randomization_draw(self, value):
        self.draw = value

    @property
    def assignment_probability(self):
        return self.probability

    @assignment_probability.setter
    def assignment_probability(self, value):
        self.probability = value

class QuestionSnapshotQuerySet(models.QuerySet):
    def _assert_not_task_backed(self, *, action):
        if self.filter(version__class_tasks__isnull=False).exists():
            raise ValidationError(f"已发布题目快照不可{action}。")

    def update(self, **kwargs):
        self._assert_not_task_backed(action="修改")
        return super().update(**kwargs)

    def delete(self):
        self._assert_not_task_backed(action="删除")
        return super().delete()


class QuestionSnapshotManager(models.Manager.from_queryset(QuestionSnapshotQuerySet)):
    pass


class QuestionSnapshot(models.Model):
    version = models.ForeignKey(ExperimentVersion, on_delete=models.CASCADE, related_name="question_snapshots")
    source_question = models.ForeignKey("question_bank.Question", on_delete=models.PROTECT)
    source_sequence = models.PositiveIntegerField()
    course_sequence = models.PositiveIntegerField(null=True, blank=True)
    stable_code = models.CharField(max_length=32, blank=True, default="")
    source_bank_id = models.PositiveBigIntegerField(null=True, blank=True)
    source_bank_name = models.CharField(max_length=160, blank=True, default="")
    bank_sequence = models.PositiveIntegerField(null=True, blank=True)
    code = models.CharField(max_length=64)
    question_type = models.CharField(max_length=24, choices=[
        ("single_choice", "单选题"),
        ("multiple_choice", "多选题"),
        ("numeric", "数值题"),
        ("short_answer", "简答题"),
        ("fill_blank", "填空题"),
        ("true_false", "判断题"),
    ])
    text = models.TextField()
    options = models.JSONField(default=list)
    correct_answer = models.JSONField()
    accepted_answers = models.JSONField(default=list)
    absolute_tolerance = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    relative_tolerance = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    explanation = models.TextField(blank=True)
    teacher_difficulty = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    empirical_difficulty = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True)
    primary_kc = models.CharField(max_length=160)
    all_kcs = models.JSONField(default=list)
    primary_kc_id_snapshot = models.PositiveBigIntegerField(null=True, blank=True)
    primary_kc_path = models.TextField(blank=True, default="")
    primary_kc_levels = models.JSONField(default=list)
    all_kc_paths = models.JSONField(default=list)
    all_kc_snapshots = models.JSONField(default=list)
    root_kc_code = models.CharField(max_length=32, blank=True, default="")
    kc_mapping_snapshot = models.JSONField(default=dict)
    kc_mapping_schema_version = models.CharField(max_length=32, blank=True, default="")
    kc_mapping_hash = models.CharField(max_length=64, blank=True, default="")
    release_fingerprint = models.CharField(max_length=64, blank=True, default="")
    objects = QuestionSnapshotManager()
    class Meta: ordering = ["source_sequence", "id"]

    def _is_task_backed(self):
        return self.version.class_tasks.exists()

    def save(self, *args, **kwargs):
        if not self._state.adding and self._is_task_backed():
            raise ValidationError("已发布题目快照不可修改。")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._is_task_backed():
            raise ValidationError("已发布题目快照不可删除。")
        return super().delete(*args, **kwargs)


class ItemInstanceQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise ValidationError("ItemInstance 呈现内容创建后不可批量修改。")

    def delete(self):
        raise ValidationError("ItemInstance 呈现内容不能由普通业务流程删除。")

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("ItemInstance 必须通过受校验的呈现服务创建。")


class ItemInstanceManager(models.Manager.from_queryset(ItemInstanceQuerySet)):
    pass


class ItemInstance(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    question_snapshot = models.ForeignKey(
        QuestionSnapshot,
        on_delete=models.CASCADE,
        related_name="item_instances",
    )
    presentation_variant = models.CharField(max_length=64, default="default")
    option_order = models.JSONField(default=list, blank=True)
    parameter_values = models.JSONField(default=dict, blank=True)
    random_seed = models.CharField(max_length=128, blank=True, default="")
    presentation_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = ItemInstanceManager()

    class Meta:
        ordering = ["question_snapshot_id", "created_at", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=["question_snapshot", "presentation_hash"],
                name="unique_question_presentation_hash",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if not isinstance(self.option_order, list):
            errors["option_order"] = "选项顺序必须是列表。"
        if not isinstance(self.parameter_values, dict):
            errors["parameter_values"] = "参数值必须是对象。"
        if self.question_snapshot_id and isinstance(self.option_order, list):
            expected_keys = presentation_option_keys(self.question_snapshot)
            if (
                any(not isinstance(key, str) for key in self.option_order)
                or len(self.option_order) != len(expected_keys)
                or set(self.option_order) != set(expected_keys)
            ):
                errors["option_order"] = "选项顺序必须是题目选项的完整无重复排列。"
        if not errors:
            expected_hash = canonical_presentation_hash(
                presentation_variant=self.presentation_variant,
                option_order=self.option_order,
                parameter_values=self.parameter_values,
                random_seed=self.random_seed,
            )
            if self.presentation_hash != expected_hash:
                errors["presentation_hash"] = "呈现哈希与规范化呈现内容不一致。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            original = type(self).objects.get(pk=self.pk)
            immutable_fields = (
                "question_snapshot_id",
                "presentation_variant",
                "option_order",
                "parameter_values",
                "random_seed",
                "presentation_hash",
            )
            if any(
                getattr(original, field) != getattr(self, field)
                for field in immutable_fields
            ):
                raise ValidationError("ItemInstance 呈现内容创建后不可修改。")
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)


class ExperimentClassRandomizationIdentity(models.Model):
    """One immutable randomization root per Experiment × Class pair."""

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.PROTECT,
        related_name="class_randomization_identities",
    )
    class_group = models.ForeignKey(
        "accounts.ClassGroup",
        on_delete=models.PROTECT,
        related_name="experiment_randomization_identities",
    )
    encrypted_root_seed = models.BinaryField(editable=False)
    root_seed_fingerprint = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64, default="task_root_hmac_v2")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_experiment_randomization_identities",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["experiment", "class_group"],
                name="unique_experiment_class_randomization_identity",
            )
        ]

    def save(self, *args, **kwargs):
        if self.pk and not self._state.adding:
            raise ValidationError("班级随机化根不可修改。")
        return super().save(*args, **kwargs)


class PublishPlan(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        PREVIEWED = "previewed", "已预览"
        COMMITTED = "committed", "已提交"
        DISCARDED = "discarded", "已放弃"
        EXPIRED = "expired", "已过期"

    experiment = models.ForeignKey(
        Experiment,
        on_delete=models.CASCADE,
        related_name="publish_plans",
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    current_step = models.PositiveSmallIntegerField(default=1)
    plan_revision = models.PositiveIntegerField(default=1)
    default_opens_at = models.DateTimeField(null=True, blank=True)
    default_closes_at = models.DateTimeField(null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_publish_plans",
    )
    current_editor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="editing_publish_plans",
    )
    last_meaningful_write_at = models.DateTimeField()
    expires_at = models.DateTimeField()
    source_plan = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="copies",
    )
    preview_run = models.ForeignKey(
        "PublishPreviewRun",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="selected_by_plans",
    )
    committed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-last_meaningful_write_at", "-pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["experiment"],
                condition=Q(status__in=["draft", "previewed"]),
                name="unique_active_publish_plan_per_experiment",
            ),
            models.CheckConstraint(
                condition=Q(current_step__gte=1, current_step__lte=2),
                name="publish_plan_step_range",
            ),
            models.CheckConstraint(
                condition=Q(default_closes_at__isnull=True)
                | Q(default_opens_at__isnull=True)
                | Q(default_closes_at__gt=models.F("default_opens_at")),
                name="publish_plan_default_schedule_order",
            ),
        ]


class PublishPlanClass(models.Model):
    publish_plan = models.ForeignKey(
        PublishPlan,
        on_delete=models.CASCADE,
        related_name="class_plans",
    )
    class_group = models.ForeignKey(
        "accounts.ClassGroup",
        on_delete=models.PROTECT,
        related_name="publish_plan_entries",
    )
    use_batch_default = models.BooleanField(default=True)
    opens_at = models.DateTimeField(null=True, blank=True)
    closes_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["class_group__academic_year", "class_group__name", "pk"]
        constraints = [
            models.UniqueConstraint(
                fields=["publish_plan", "class_group"],
                name="unique_publish_plan_class",
            ),
            models.CheckConstraint(
                condition=Q(closes_at__isnull=True)
                | Q(opens_at__isnull=True)
                | Q(closes_at__gt=models.F("opens_at")),
                name="publish_plan_class_schedule_order",
            ),
        ]


class PublishPlanExclusion(models.Model):
    publish_plan_class = models.ForeignKey(
        PublishPlanClass,
        on_delete=models.CASCADE,
        related_name="exclusions",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="publish_plan_exclusions",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["publish_plan_class", "student"],
                name="unique_publish_plan_class_exclusion",
            )
        ]


class PublishPreviewRun(models.Model):
    class Status(models.TextChoices):
        VALID = "valid", "有效"
        INVALIDATED = "invalidated", "已失效"

    publish_plan = models.ForeignKey(
        PublishPlan,
        on_delete=models.CASCADE,
        related_name="preview_runs",
    )
    plan_revision = models.PositiveIntegerField()
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.VALID)
    definition_fingerprint = models.CharField(max_length=64)
    input_hash = models.CharField(max_length=64)
    output_hash = models.CharField(max_length=64)
    algorithm_version = models.CharField(max_length=64, default="task_root_hmac_v2")
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="created_publish_preview_runs",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.CharField(max_length=64, blank=True, default="")


class PublishPreviewClassResult(models.Model):
    preview_run = models.ForeignKey(
        PublishPreviewRun,
        on_delete=models.CASCADE,
        related_name="class_results",
    )
    publish_plan_class = models.ForeignKey(
        PublishPlanClass,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="preview_results",
    )
    randomization_identity = models.ForeignKey(
        ExperimentClassRandomizationIdentity,
        on_delete=models.PROTECT,
        related_name="preview_results",
    )
    participant_count = models.PositiveIntegerField(default=0)
    input_hash = models.CharField(max_length=64)
    output_hash = models.CharField(max_length=64)
    quota_valid = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["preview_run", "publish_plan_class"],
                name="unique_preview_run_class_result",
            )
        ]


class PublishPreviewParticipant(models.Model):
    class_result = models.ForeignKey(
        PublishPreviewClassResult,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="publish_preview_participations",
    )
    course_subject = models.ForeignKey(
        "accounts.CourseSubject",
        on_delete=models.PROTECT,
        related_name="publish_preview_participations",
    )
    randomization_subject_key = models.CharField(max_length=128)
    sampling_seed = models.CharField(max_length=64)
    ordering_seed = models.CharField(max_length=64)
    sampling_algorithm_version = models.CharField(
        max_length=64,
        default="task_root_hmac_v2",
    )
    item_count = models.PositiveIntegerField(default=0)
    is_valid = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["class_result", "student"],
                name="unique_preview_class_student",
            )
        ]


class ReleaseSnapshotQuestionMapping(models.Model):
    release_snapshot = models.ForeignKey(
        ExperimentVersion,
        on_delete=models.CASCADE,
        related_name="question_mappings",
    )
    source_question = models.ForeignKey(
        "question_bank.Question",
        on_delete=models.PROTECT,
        related_name="release_snapshot_mappings",
    )
    question_release_fingerprint = models.CharField(max_length=64)
    question_snapshot = models.ForeignKey(
        QuestionSnapshot,
        on_delete=models.PROTECT,
        related_name="release_mappings",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["release_snapshot", "source_question"],
                name="unique_release_source_question_mapping",
            ),
            models.UniqueConstraint(
                fields=["release_snapshot", "question_snapshot"],
                name="unique_release_question_snapshot_mapping",
            ),
        ]


class PublishPreviewItem(models.Model):
    participant = models.ForeignKey(
        PublishPreviewParticipant,
        on_delete=models.CASCADE,
        related_name="items",
    )
    source_question = models.ForeignKey(
        "question_bank.Question",
        on_delete=models.PROTECT,
        related_name="publish_preview_items",
    )
    question_release_fingerprint = models.CharField(max_length=64)
    assigned_sequence_index = models.PositiveIntegerField()
    sampling_source = models.JSONField(default=dict, blank=True)
    release_mapping = models.ForeignKey(
        ReleaseSnapshotQuestionMapping,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="preview_items",
    )

    class Meta:
        ordering = ["assigned_sequence_index"]
        constraints = [
            models.UniqueConstraint(
                fields=["participant", "assigned_sequence_index"],
                name="unique_preview_item_sequence",
            ),
            models.UniqueConstraint(
                fields=["participant", "source_question"],
                name="unique_preview_participant_source_question",
            ),
        ]


class PublishPreviewInvalidation(models.Model):
    preview_run = models.ForeignKey(
        PublishPreviewRun,
        on_delete=models.CASCADE,
        related_name="invalidation_events",
    )
    reason = models.CharField(max_length=64)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="publish_preview_invalidations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
