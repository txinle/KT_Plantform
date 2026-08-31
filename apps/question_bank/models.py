from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Q
from django.utils import timezone
from datetime import timedelta
import uuid


QUESTION_BANK_COURSE_IMMUTABLE_MESSAGE = "已创建题库的所属课程不可修改。"


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

    def _validate_course_is_immutable(self, using=None):
        if self.pk is None:
            return
        database = using or self._state.db or "default"
        existing = (
            type(self)._default_manager.using(database)
            .filter(pk=self.pk)
            .values("course_id")
            .first()
        )
        if existing is not None and existing["course_id"] != self.course_id:
            raise ValidationError({"course": QUESTION_BANK_COURSE_IMMUTABLE_MESSAGE})

    def clean(self):
        super().clean()
        self._validate_course_is_immutable()

    def save(self, *args, **kwargs):
        self._validate_course_is_immutable(using=kwargs.get("using"))
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
    class Status(models.TextChoices):
        ACTIVE = "active", "可用"
        ARCHIVED = "archived", "归档"
    code = models.CharField(max_length=32, blank=True, default="")
    name = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.ACTIVE,
    )
    normalized_name = models.CharField(max_length=160)
    course = models.ForeignKey(
        "accounts.Course",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="concepts",
    )
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
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
            models.UniqueConstraint(
                fields=["course", "code"],
                condition=Q(course__isnull=False),
                name="unique_course_kc_code",
            ),
            models.CheckConstraint(
                condition=Q(course__isnull=True) | Q(code__regex=r"^KC[1-9][0-9]*$"),
                name="course_kc_code_canonical",
            ),
        ]

    def clean(self):
        super().clean()
        if self.course_id is not None:
            from .services.course_kc import validate_course_kc_code

            validate_course_kc_code(self.code)
            duplicate = type(self).objects.filter(
                course_id=self.course_id,
                normalized_name=self.normalized_name,
            )
            if self.pk is not None:
                duplicate = duplicate.exclude(pk=self.pk)
            if duplicate.exists():
                raise ValidationError({"name": "当前课程已存在规范化后同名的 KC。"})
        if self.course_id is not None and self.parent_id is not None:
            raise ValidationError({"parent": "课程 KC 不保存固定父子关系。"})
        if self.parent_id is None:
            return
        if self.pk is not None and self.parent_id == self.pk:
            raise ValidationError({"parent": "KC 不能将自身设为父节点。"})
        if self.parent.course_id != self.course_id:
            raise ValidationError({"parent": "父 KC 必须属于同一课程。"})
        if self.parent.question_links.exists():
            raise ValidationError(
                {"parent": "已有题目映射的 KC 必须保持为叶节点。"}
            )
        ancestor = self.parent
        while ancestor is not None:
            if self.pk is not None and ancestor.pk == self.pk:
                raise ValidationError({"parent": "KC 父子关系不能形成循环。"})
            ancestor = ancestor.parent

    def save(self, *args, **kwargs):
        from .services.course_kc import (
            allocate_next_course_kc_code,
            normalize_course_kc_name,
        )

        self.normalized_name = normalize_course_kc_name(self.name)
        if self.course_id is not None:
            self.scope = self.Scope.PRIVATE
            self.owner = None
        elif self.scope == self.Scope.PUBLIC:
            self.owner = None
        with transaction.atomic():
            if self.course_id is not None and not self.code:
                self.code = allocate_next_course_kc_code(course_id=self.course_id)
            if self.pk is not None:
                original_code = type(self).objects.filter(pk=self.pk).values_list(
                    "code", flat=True
                ).first()
                if original_code and self.code != original_code:
                    raise ValidationError({"code": "kc_code 创建后不可修改。"})
            if self.parent_id is not None:
                self.parent = type(self).objects.select_for_update().get(
                    pk=self.parent_id
                )
            self.clean()
            super().save(*args, **kwargs)

    @property
    def path_names(self):
        names = []
        current = self
        while current is not None:
            names.append(current.name)
            current = current.parent
        return list(reversed(names))

    @property
    def full_path(self):
        return " / ".join(self.path_names)

    @property
    def depth(self):
        return len(self.path_names)

    @property
    def is_leaf(self):
        return not self.children.exists()

    def __str__(self): return self.name


def import_preview_expiry():
    return timezone.now() + timedelta(minutes=30)


class ImportPreviewBatch(models.Model):
    class Kind(models.TextChoices):
        COURSE_KC = "course_kc", "课程 KC"
        QUESTION_KC = "question_kc", "题目 KC"

    class Status(models.TextChoices):
        PENDING = "pending", "待确认"
        CONSUMED = "consumed", "已消费"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    kind = models.CharField(max_length=24, choices=Kind.choices)
    course = models.ForeignKey(
        "accounts.Course",
        on_delete=models.CASCADE,
        related_name="question_bank_import_previews",
    )
    bank = models.ForeignKey(
        QuestionBank,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="import_previews",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="question_bank_import_previews",
    )
    session_binding = models.CharField(max_length=64)
    file_sha256 = models.CharField(max_length=64)
    payload = models.JSONField(default=dict)
    summary = models.JSONField(default=dict)
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.PENDING,
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=import_preview_expiry)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "expires_at"], name="qb_preview_status_exp"),
        ]

class QuestionQuerySet(models.QuerySet):
    def update(self, **kwargs):
        if (
            {"bank", "bank_id"}.intersection(kwargs)
            and kwargs.get("status") != Question.Status.DRAFT
            and self.filter(
                status=Question.Status.ACTIVE,
                bank__course__isnull=False,
            ).exists()
        ):
            raise ValidationError(
                "课程可用题目不可批量换绑题库；请先转为草稿。"
            )
        if kwargs.get("status") == Question.Status.ACTIVE:
            with transaction.atomic(using=self.db):
                questions = list(
                    self.select_for_update().select_related("bank").order_by("pk")
                )
                for question in questions:
                    question.status = Question.Status.ACTIVE
                    question._validate_active_course_mapping()
                return models.QuerySet.update(
                    self.filter(pk__in=[question.pk for question in questions]),
                    **kwargs,
                )
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        for question in objs:
            if (
                question.status == Question.Status.ACTIVE
                and question.bank.course_id is not None
            ):
                question.status = Question.Status.DRAFT
        return super().bulk_create(objs, **kwargs)


class QuestionManager(models.Manager.from_queryset(QuestionQuerySet)):
    pass


class Question(models.Model):
    class Type(models.TextChoices):
        SINGLE = "single_choice", "单选题"
        MULTIPLE = "multiple_choice", "多选题"
        NUMERIC = "numeric", "数值题"
        SHORT = "short_answer", "简答题"
        FILL_BLANK = "fill_blank", "填空题"
        TRUE_FALSE = "true_false", "判断题"
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
    objects = QuestionManager()
    class Meta:
        ordering = ["sequence", "id"]
        constraints = [models.UniqueConstraint(fields=["bank", "sequence"], name="unique_question_sequence_in_bank")]
    def save(self, *args, **kwargs):
        if not self.code:
            self.code = f"Q{self.sequence:04d}"
        if (
            self._state.adding
            and self.status == self.Status.ACTIVE
            and self.bank.course_id is not None
        ):
            self.status = self.Status.DRAFT
            self._activate_after_primary_mapping = True
        elif self.status == self.Status.ACTIVE and self.bank.course_id is not None:
            self._validate_active_course_mapping()
        with transaction.atomic():
            super().save(*args, **kwargs)
            # Import locally so the allocator can import this model without a module cycle.
            from .services.course_question_identity import ensure_course_question_identity

            ensure_course_question_identity(self)

    def _validate_active_course_mapping(self):
        primary_links = list(
            self.concept_links.filter(is_primary=True).select_related("concept")
        )
        if len(primary_links) != 1:
            raise ValidationError("课程可用题目必须有且仅有一个主 KC。")
        links = list(self.concept_links.select_related("concept"))
        if any(link.concept.course_id != self.bank.course_id for link in links):
            raise ValidationError("题目 KC 必须属于题库所在课程。")
        if any(link.concept.children.exists() for link in links):
            raise ValidationError("课程可用题目只能映射到叶节点 KC。")
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

class QuestionConceptQuerySet(models.QuerySet):
    STRUCTURAL_FIELDS = {
        "question", "question_id", "concept", "concept_id", "is_primary",
        "parent_link", "parent_link_id", "display_order",
    }

    def _assert_primary_is_not_removed_from_active_question(self):
        if self.filter(
            is_primary=True,
            question__status=Question.Status.ACTIVE,
            question__bank__course__isnull=False,
        ).exists():
            raise ValidationError("可用题目的主 KC 不可直接删除或改写；请先转为草稿。")

    def update(self, **kwargs):
        if self.STRUCTURAL_FIELDS.intersection(kwargs):
            raise ValidationError("题目 KC 映射不支持批量改写。")
        return super().update(**kwargs)

    def bulk_create(self, objs, **kwargs):
        raise ValidationError("题目 KC 映射必须逐条通过叶节点与课程校验。")

    def delete(self):
        self._assert_primary_is_not_removed_from_active_question()
        return super().delete()


class QuestionConceptManager(models.Manager.from_queryset(QuestionConceptQuerySet)):
    pass


class QuestionConcept(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="concept_links")
    concept = models.ForeignKey(Concept, on_delete=models.PROTECT, related_name="question_links")
    is_primary = models.BooleanField(default=False)
    parent_link = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="child_links",
    )
    display_order = models.PositiveSmallIntegerField(default=1)
    objects = QuestionConceptManager()
    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["question", "concept"], name="unique_question_concept"),
            models.UniqueConstraint(fields=["question"], condition=Q(is_primary=True), name="one_primary_concept_per_question"),
            models.CheckConstraint(
                condition=Q(display_order__gt=0),
                name="question_concept_display_order_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if (
            self.question_id is not None
            and self.question.bank.course_id is not None
            and self.concept.course_id != self.question.bank.course_id
        ):
            raise ValidationError({"concept": "题目 KC 必须属于题库所在课程。"})
        is_course_mapping = (
            self.question_id is not None
            and self.question.bank.course_id is not None
        )
        if is_course_mapping and self.parent_link_id is None and not self.is_primary:
            raise ValidationError({"is_primary": "题目 KC 树的根节点必须是主 KC。"})
        if self.parent_link_id is not None:
            if self.is_primary:
                raise ValidationError({"is_primary": "非根节点不能标记为主 KC。"})
            if self.parent_link.question_id != self.question_id:
                raise ValidationError({"parent_link": "父 KC 必须属于同一道题。"})
        if self.concept_id is not None and self.concept.status == Concept.Status.ARCHIVED:
            raise ValidationError({"concept": "已归档 KC 不能加入新的题目映射。"})

    def save(self, *args, **kwargs):
        with transaction.atomic():
            if self.pk and self.question.status == Question.Status.ACTIVE:
                raise ValidationError(
                    "可用题目的 KC 映射不可直接改写；请先转为草稿。"
                )
            if self.concept_id is not None:
                self.concept = Concept.objects.select_for_update().get(
                    pk=self.concept_id
                )
            self.clean()
            result = super().save(*args, **kwargs)
            question = self.question
            if (
                self.is_primary
                and getattr(question, "_activate_after_primary_mapping", False)
            ):
                question.status = Question.Status.ACTIVE
                question.save(update_fields=["status"])
                del question._activate_after_primary_mapping
            return result

    def delete(self, *args, **kwargs):
        if (
            self.is_primary
            and self.question.status == Question.Status.ACTIVE
            and self.question.bank.course_id is not None
        ):
            raise ValidationError(
                "可用题目的主 KC 不可直接删除；请先转为草稿。"
            )
        return super().delete(*args, **kwargs)
