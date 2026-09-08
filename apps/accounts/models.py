import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models, router, transaction


class Course(models.Model):
    class Status(models.TextChoices):
        ACTIVE = "active", "进行中"
        ARCHIVED = "archived", "已归档"

    name = models.CharField("课程名称", max_length=160)
    code = models.CharField("课程代码", max_length=64)
    term = models.CharField("学期", max_length=64)
    description = models.TextField("课程说明", blank=True)
    owner = models.ForeignKey(
        "User",
        on_delete=models.PROTECT,
        related_name="owned_courses",
        limit_choices_to={"role": "teacher"},
    )
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-term", "code", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["owner", "code", "term"],
                name="unique_course_owner_code_term",
            )
        ]

    def __str__(self):
        return f"{self.code} · {self.name} · {self.term}"

    def can_manage(self, user, *, using=None, lock_teacher_link=False):
        if not getattr(user, "is_authenticated", False) or not user.is_active:
            return False
        if user.is_superuser or user.role == User.Role.ADMIN:
            return True
        if user.role != User.Role.TEACHER:
            return False
        if self.owner_id == user.id:
            return True
        teacher_links = self.teacher_links
        if using is not None:
            teacher_links = teacher_links.using(using)
        teacher_link = teacher_links.filter(teacher_id=user.pk)
        if lock_teacher_link:
            return teacher_link.select_for_update().first() is not None
        return teacher_link.exists()


class CourseTeacher(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="teacher_links")
    teacher = models.ForeignKey("User", on_delete=models.PROTECT, related_name="course_links")
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["course", "teacher"],
                name="unique_course_teacher",
            )
        ]

    def clean(self):
        super().clean()
        if self.teacher_id and self.teacher.role != User.Role.TEACHER:
            raise ValidationError({"teacher": "课程协作成员必须是教师账号。"})
        if self.course_id and self.teacher_id == self.course.owner_id:
            raise ValidationError({"teacher": "课程负责人无需重复添加为协作教师。"})


class ClassGroupQuerySet(models.QuerySet):
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
            if locked_targets.filter(experiment_tasks__isnull=False).exists():
                raise ValidationError("已存在正式班级实验任务，班级所属课程不可变更。")
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
        objs = list(objs)
        update_fields = tuple(update_fields) if update_fields is not None else None
        if ignore_conflicts:
            raise ValidationError("班级批量创建不支持忽略冲突。")
        # Non-course conflict upserts remain a public write surface; course
        # field upserts are rejected. No current application caller uses it.
        if update_conflicts and self._writes_course(update_fields or ()):
            raise ValidationError("班级不支持批量冲突更新。")
        self._for_write = True
        using = self.db
        with transaction.atomic(using=using):
            created = super().bulk_create(
                objs,
                batch_size=batch_size,
                ignore_conflicts=ignore_conflicts,
                update_conflicts=update_conflicts,
                update_fields=update_fields,
                unique_fields=unique_fields,
            )
            class_group_ids = [class_group.pk for class_group in created]
            if any(class_group_id is None for class_group_id in class_group_ids):
                raise ValidationError("班级批量创建未返回稳定标识。")
            if class_group_ids:
                ClassStudentAnalyticsPolicy.objects.using(using).bulk_create(
                    [
                        ClassStudentAnalyticsPolicy(class_group_id=class_group_id)
                        for class_group_id in class_group_ids
                    ],
                    ignore_conflicts=True,
                )
            return created


class ClassGroupManager(models.Manager.from_queryset(ClassGroupQuerySet)):
    pass


class ClassGroup(models.Model):
    name = models.CharField("班级", max_length=120)
    academic_year = models.CharField(
        "年级",
        max_length=32,
        blank=True,
        validators=[
            RegexValidator(
                regex=r"^\d{4}$",
                message="年级必须填写四位年份，如：2025。",
            )
        ],
    )
    owner = models.ForeignKey("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_classes")
    course = models.ForeignKey(
        Course,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="classes",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    objects = ClassGroupManager()

    class Meta:
        ordering = ["academic_year", "name"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "name", "academic_year"],
                condition=models.Q(course__isnull=False),
                name="unique_course_class_year",
            ),
            models.UniqueConstraint(
                fields=["name", "academic_year"],
                condition=models.Q(course__isnull=True),
                name="unique_legacy_class_year",
            ),
        ]
        base_manager_name = "objects"
        default_manager_name = "objects"

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

    def save(self, *args, **kwargs):
        using = kwargs.get("using") or router.db_for_write(type(self), instance=self)
        save_kwargs = {**kwargs, "using": using}
        if self._state.adding:
            with transaction.atomic(using=using):
                return super().save(*args, **save_kwargs)
        if not self._task_backed_course_is_changing(
            save_kwargs.get("update_fields"),
            using=using,
        ):
            return super().save(*args, **save_kwargs)
        with transaction.atomic(using=using):
            locked = (
                self.__class__._base_manager.using(using)
                .filter(pk=self.pk)
                .select_for_update()
                .get()
            )
            if locked.course_id != self.course_id and locked.experiment_tasks.exists():
                raise ValidationError("已存在正式班级实验任务，班级所属课程不可变更。")
            return super().save(*args, **save_kwargs)

    def __str__(self):
        return f"{self.academic_year} {self.name}".strip()


class ClassStudentAnalyticsPolicy(models.Model):
    class RankMode(models.TextChoices):
        NONE = "none", "不展示"
        SELF = "self", "仅本人"

    class_group = models.OneToOneField(
        ClassGroup,
        on_delete=models.PROTECT,
        related_name="student_analytics_policy",
    )
    show_scores = models.BooleanField("展示分数", default=True)
    show_correct_rate = models.BooleanField("展示正确率", default=True)
    show_item_results = models.BooleanField("展示题目结果", default=True)
    show_class_average = models.BooleanField("展示班级均值", default=True)
    rank_mode = models.CharField(
        "排名展示方式",
        max_length=16,
        choices=RankMode.choices,
        default=RankMode.SELF,
    )
    show_kc_rates = models.BooleanField("展示知识点正确率", default=True)
    show_trend = models.BooleanField("展示学习趋势", default=True)
    updated_by = models.ForeignKey(
        "User",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="updated_student_analytics_policies",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "班级学生分析策略"
        verbose_name_plural = "班级学生分析策略"

    def __str__(self):
        return f"班级 #{self.class_group_id} 学生分析策略"


class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "管理员"
        TEACHER = "teacher", "教师"
        STUDENT = "student", "学生"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    display_name = models.CharField("姓名", max_length=80, blank=True)
    student_number = models.CharField("学号", max_length=64, unique=True, null=True, blank=True)
    must_change_password = models.BooleanField("必须修改密码", default=False)
    class_group = models.ForeignKey(ClassGroup, null=True, blank=True, on_delete=models.SET_NULL, related_name="students")

    @property
    def shown_name(self):
        return self.display_name or self.get_full_name() or self.username

    def save(self, *args, **kwargs):
        if self.role == self.Role.STUDENT and self.student_number and not self.username:
            self.username = self.student_number
        if self.role != self.Role.STUDENT:
            self.student_number = None
        if self.role == self.Role.ADMIN:
            self.is_staff = True
        super().save(*args, **kwargs)


class CourseEnrollment(models.Model):
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="enrollments")
    student = models.ForeignKey(User, on_delete=models.PROTECT, related_name="course_enrollments")
    class_group = models.ForeignKey(
        ClassGroup,
        on_delete=models.PROTECT,
        related_name="enrollments",
    )
    is_active = models.BooleanField(default=True)
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["class_group__name", "student__student_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "student"],
                name="unique_course_student_enrollment",
            )
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.student_id and self.student.role != User.Role.STUDENT:
            errors["student"] = "选课对象必须是学生账号。"
        if self.course_id and self.class_group_id and self.class_group.course_id != self.course_id:
            errors["class_group"] = "班级必须属于同一课程。"
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        return f"{self.course} / {self.class_group} / {self.student.shown_name}"

    @transaction.atomic
    def save(self, *args, **kwargs):
        result = super().save(*args, **kwargs)
        CourseSubject.objects.get_or_create(
            course_id=self.course_id,
            student_id=self.student_id,
        )
        return result


class CourseSubject(models.Model):
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="research_subjects",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="course_subjects",
        limit_choices_to={"role": User.Role.STUDENT},
    )
    public_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["course_id", "student_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["course", "student"],
                name="unique_course_subject",
            )
        ]

    def __str__(self):
        return str(self.public_uuid)


class ResearchStudy(models.Model):
    public_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    name = models.CharField(max_length=180)
    description = models.TextField(blank=True)
    owner = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="owned_research_studies",
        limit_choices_to={"role": User.Role.TEACHER},
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "name"]

    def __str__(self):
        return self.name


class ResearchStudyCourse(models.Model):
    study = models.ForeignKey(
        ResearchStudy,
        on_delete=models.CASCADE,
        related_name="course_links",
    )
    course = models.ForeignKey(
        Course,
        on_delete=models.PROTECT,
        related_name="research_study_links",
    )
    added_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["study", "course"],
                name="unique_research_study_course",
            )
        ]


class ResearchStudyParticipant(models.Model):
    study = models.ForeignKey(
        ResearchStudy,
        on_delete=models.CASCADE,
        related_name="participants",
    )
    student = models.ForeignKey(
        User,
        on_delete=models.PROTECT,
        related_name="research_study_participations",
        limit_choices_to={"role": User.Role.STUDENT},
    )
    public_uuid = models.UUIDField(default=uuid.uuid4, editable=False, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["study_id", "student_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["study", "student"],
                name="unique_research_study_participant",
            )
        ]

    def __str__(self):
        return str(self.public_uuid)


class DataPurgeRequest(models.Model):
    class Status(models.TextChoices):
        PREVIEW = "preview", "待确认"
        APPROVED = "approved", "已确认"
        EXECUTED = "executed", "已执行"
        FAILED = "failed", "失败"

    purge_request_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    scope_type = models.CharField(max_length=32, default="course")
    scope_definition = models.JSONField(default=dict)
    reason_code = models.CharField(max_length=64)
    requested_by = models.ForeignKey(User, on_delete=models.PROTECT, related_name="data_purge_requests")
    requested_at = models.DateTimeField(auto_now_add=True)
    preview_counts = models.JSONField(default=dict)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PREVIEW)
    executed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.PROTECT, related_name="executed_data_purges")
    executed_at = models.DateTimeField(null=True, blank=True)
    result_counts = models.JSONField(default=dict)

    class Meta:
        ordering = ["-requested_at"]
