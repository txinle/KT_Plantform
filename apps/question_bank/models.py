from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import Q

class QuestionBank(models.Model):
    class Visibility(models.TextChoices):
        PRIVATE = "private", "私有"
        SHARED = "shared", "共享"
        ARCHIVED = "archived", "归档"
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True)
    course = models.ForeignKey(
        "accounts.Course",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="question_banks",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="created_question_banks",
    )
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="question_banks",
    )
    visibility = models.CharField(max_length=16, choices=Visibility.choices, default=Visibility.PRIVATE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    class Meta:
        ordering = ["-updated_at"]
    def save(self, *args, **kwargs):
        if self.created_by_id is None and self.owner_id is not None:
            self.created_by_id = self.owner_id
        if self.owner_id is None and self.created_by_id is not None:
            self.owner_id = self.created_by_id
        super().save(*args, **kwargs)
    def __str__(self): return self.name

class Concept(models.Model):
    class Scope(models.TextChoices):
        PUBLIC = "public", "公共"
        PRIVATE = "private", "私有"
    name = models.CharField(max_length=160)
    normalized_name = models.CharField(max_length=160)
    course = models.ForeignKey(
        "accounts.Course",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="concepts",
    )
    scope = models.CharField(max_length=16, choices=Scope.choices, default=Scope.PRIVATE)
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.CASCADE, related_name="concepts")
    created_at = models.DateTimeField(auto_now_add=True)
    class Meta:
        ordering = ["name"]
        constraints = [
            models.UniqueConstraint(fields=["normalized_name"], condition=Q(scope="public"), name="unique_public_concept"),
            models.UniqueConstraint(fields=["owner", "normalized_name"], condition=Q(scope="private"), name="unique_private_concept"),
            models.UniqueConstraint(
                fields=["course", "normalized_name"],
                condition=Q(course__isnull=False),
                name="unique_course_concept",
            ),
        ]
    def save(self, *args, **kwargs):
        self.normalized_name = " ".join(self.name.strip().casefold().split())
        if self.course_id is not None:
            self.scope = self.Scope.PRIVATE
            self.owner = None
        elif self.scope == self.Scope.PUBLIC:
            self.owner = None
        super().save(*args, **kwargs)
    def __str__(self): return self.name

class Question(models.Model):
    class Type(models.TextChoices):
        SINGLE = "single_choice", "单选题"
        MULTIPLE = "multiple_choice", "多选题"
        NUMERIC = "numeric", "数值题"
        SHORT = "short_answer", "简答题"
    class Status(models.TextChoices):
        DRAFT = "draft", "草稿"
        ACTIVE = "active", "可用"
        ARCHIVED = "archived", "归档"
    bank = models.ForeignKey(QuestionBank, on_delete=models.CASCADE, related_name="questions")
    sequence = models.PositiveIntegerField()
    code = models.CharField(max_length=64, blank=True)
    question_type = models.CharField(max_length=24, choices=Type.choices)
    text = models.TextField()
    correct_answer = models.JSONField()
    accepted_answers = models.JSONField(default=list, blank=True)
    absolute_tolerance = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    relative_tolerance = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    explanation = models.TextField(blank=True)
    teacher_difficulty = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(1)])
    empirical_difficulty = models.DecimalField(max_digits=4, decimal_places=3, null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(1)])
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    concepts = models.ManyToManyField(Concept, through="QuestionConcept", related_name="questions")
    class Meta:
        ordering = ["sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["bank", "sequence"], name="unique_question_sequence_in_bank")]
    def save(self, *args, **kwargs):
        if not self.code: self.code = f"Q{self.sequence:04d}"
        super().save(*args, **kwargs)
    @property
    def primary_concept(self):
        link = self.concept_links.filter(is_primary=True).select_related("concept").first()
        return link.concept if link else None
    def __str__(self): return f"{self.code} {self.text[:40]}"


class CourseQuestionIdentity(models.Model):
    course = models.ForeignKey(
        "accounts.Course",
        on_delete=models.PROTECT,
        related_name="question_identities",
    )
    question = models.OneToOneField(
        Question,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="course_identity",
    )
    course_sequence = models.PositiveIntegerField(validators=[MinValueValidator(1)])
    stable_code = models.CharField(max_length=32, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["course_id", "course_sequence"]
        constraints = [
            models.CheckConstraint(
                condition=Q(course_sequence__gt=0),
                name="course_question_identity_sequence_positive",
            ),
            models.UniqueConstraint(
                fields=["course", "course_sequence"],
                name="unique_course_question_identity_sequence",
            ),
        ]


class QuestionOption(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="options")
    key = models.CharField(max_length=1)
    text = models.TextField()
    display_order = models.PositiveSmallIntegerField(default=1)
    class Meta:
        ordering = ["display_order", "key"]
        constraints = [models.UniqueConstraint(fields=["question", "key"], name="unique_option_key")]
    def __str__(self): return f"{self.key}. {self.text}"

class QuestionConcept(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="concept_links")
    concept = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="question_links")
    is_primary = models.BooleanField(default=False)
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["question", "concept"], name="unique_question_concept"),
            models.UniqueConstraint(fields=["question"], condition=Q(is_primary=True), name="one_primary_concept_per_question"),
        ]
