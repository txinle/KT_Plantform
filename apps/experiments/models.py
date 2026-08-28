import hashlib
import json
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, router, transaction
from django.db.models import Q


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

    def _locked_course_write_target_ids(self):
        return list(
            self.select_for_update()
            .order_by("pk")
            .values_list("pk", flat=True)
        )

    def update(self, **kwargs):
        if not self._writes_course(kwargs):
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
            if locked_targets.filter(class_tasks__isnull=False).exists():
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
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
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
    class Feedback(models.TextChoices):
        NONE = "none", "仅显示已提交"
        CORRECTNESS = "correctness", "显示正误"
        FULL = "full", "显示正误、答案与解析"
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
    question_bank = models.ForeignKey("question_bank.QuestionBank", on_delete=models.PROTECT, related_name="experiments")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.DRAFT)
    selection_mode = models.CharField(
        max_length=16,
        choices=SelectionMode.choices,
        default=SelectionMode.ALL,
    )
    ordering_mode = models.CharField(max_length=24, choices=Ordering.choices, default=Ordering.FIXED)
    feedback_mode = models.CharField(max_length=16, choices=Feedback.choices, default=Feedback.NONE)
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
    confidence_scale_version = models.CharField(max_length=64, default="v1")
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

    def clean(self):
        super().clean()
        errors = {}
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

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        save_kwargs = {**kwargs, "using": using}
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
            raise ValidationError("班级实验任务不可修改。")
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
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["version_id", "student_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["version", "student"],
                name="unique_version_participant_snapshot",
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
    class Meta: ordering = ["source_sequence", "id"]


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
