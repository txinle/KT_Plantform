import uuid

from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.db import models


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

    def can_manage(self, user):
        if not getattr(user, "is_authenticated", False):
            return False
        if user.is_superuser or user.role == User.Role.ADMIN or self.owner_id == user.id:
            return True
        return self.teacher_links.filter(teacher=user).exists()


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

    def __str__(self):
        return f"{self.academic_year} {self.name}".strip()

class User(AbstractUser):
    class Role(models.TextChoices):
        ADMIN = "admin", "管理员"
        TEACHER = "teacher", "教师"
        STUDENT = "student", "学生"

    role = models.CharField(max_length=16, choices=Role.choices, default=Role.STUDENT)
    display_name = models.CharField("姓名", max_length=80, blank=True)
    student_number = models.CharField("学号", max_length=64, unique=True, null=True, blank=True)
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
