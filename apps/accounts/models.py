from django.contrib.auth.models import AbstractUser
from django.db import models

class ClassGroup(models.Model):
    name = models.CharField("班级", max_length=120)
    academic_year = models.CharField("学年", max_length=32, blank=True)
    owner = models.ForeignKey("User", null=True, blank=True, on_delete=models.SET_NULL, related_name="owned_classes")
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["academic_year", "name"]
        constraints = [models.UniqueConstraint(fields=["name", "academic_year"], name="unique_class_year")]

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
