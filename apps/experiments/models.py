from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

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
    ordering_mode = models.CharField(max_length=24, choices=Ordering.choices, default=Ordering.FIXED)
    feedback_mode = models.CharField(max_length=16, choices=Feedback.choices, default=Feedback.NONE)
    time_mode = models.CharField(max_length=20, choices=TimeMode.choices, default=TimeMode.NONE)
    total_time_limit_minutes = models.PositiveIntegerField(null=True, blank=True)
    per_question_time_limit_seconds = models.PositiveIntegerField(null=True, blank=True)
    resume_mode = models.CharField(max_length=20, choices=ResumeMode.choices, default=ResumeMode.RESUMABLE)
    questions = models.ManyToManyField("question_bank.Question", through="ExperimentQuestion", related_name="experiments")
    students = models.ManyToManyField(settings.AUTH_USER_MODEL, through="ExperimentStudent", related_name="assigned_experiments")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta: ordering = ["-updated_at"]
    def __str__(self): return self.name

    def clean(self):
        super().clean()
        if self.course_id and self.question_bank_id and self.question_bank.course_id != self.course_id:
            raise ValidationError({"question_bank": "实验只能使用同一课程的题库。"})

    def save(self, *args, **kwargs):
        if self.created_by_id is None and self.owner_id is not None:
            self.created_by_id = self.owner_id
        if self.owner_id is None and self.created_by_id is not None:
            self.owner_id = self.created_by_id
        super().save(*args, **kwargs)

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

class ExperimentVersion(models.Model):
    experiment = models.ForeignKey(Experiment, on_delete=models.PROTECT, related_name="versions")
    version_number = models.PositiveIntegerField()
    configuration = models.JSONField(default=dict)
    course_name_snapshot = models.CharField(max_length=160, blank=True)
    course_code_snapshot = models.CharField(max_length=64, blank=True)
    course_term_snapshot = models.CharField(max_length=64, blank=True)
    published_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    published_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["experiment", "version_number"], name="unique_experiment_version")]
    def __str__(self): return f"{self.experiment} v{self.version_number}"

class QuestionSnapshot(models.Model):
    version = models.ForeignKey(ExperimentVersion, on_delete=models.CASCADE, related_name="question_snapshots")
    source_question = models.ForeignKey("question_bank.Question", on_delete=models.PROTECT)
    source_sequence = models.PositiveIntegerField()
    code = models.CharField(max_length=64)
    question_type = models.CharField(max_length=24, choices=[
        ("single_choice", "单选题"),
        ("multiple_choice", "多选题"),
        ("numeric", "数值题"),
        ("short_answer", "简答题"),
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
