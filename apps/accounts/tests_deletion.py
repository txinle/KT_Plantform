from django.test import Client, TestCase
from django.urls import reverse

from apps.audit.models import AuditLog

from .models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User


class ClassDeletionTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="class-delete-owner", password="password123", role=User.Role.TEACHER
        )
        self.collaborator = User.objects.create_user(
            username="class-delete-collab", password="password123", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            name="班级删除课程", code="CLSDEL", term="2026春", owner=self.owner
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.class_group = ClassGroup.objects.create(
            course=self.course, owner=self.owner, name="空班级", academic_year="2026"
        )

    def test_delete_requires_explicit_second_confirmation(self):
        self.client.force_login(self.owner)
        url = reverse("courses:class_delete", args=[self.course.pk, self.class_group.pk])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=self.class_group.pk).exists())

    def test_collaborator_can_delete_empty_class_after_confirmation(self):
        self.client.force_login(self.collaborator)
        url = reverse("courses:class_delete", args=[self.course.pk, self.class_group.pk])

        response = self.client.post(url, {"confirmation": "delete"})

        self.assertRedirects(response, reverse("courses:classes", args=[self.course.pk]))
        self.assertFalse(ClassGroup.objects.filter(pk=self.class_group.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="class.delete").exists())

    def test_class_with_enrollment_is_protected(self):
        student = User.objects.create_user(
            username="class-delete-student",
            student_number="class-delete-student",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=student,
        )
        self.client.force_login(self.owner)
        url = reverse("courses:class_delete", args=[self.course.pk, self.class_group.pk])

        response = self.client.post(url, {"confirmation": "delete"})

        self.assertEqual(response.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=self.class_group.pk).exists())

    def test_class_delete_is_csrf_protected(self):
        self.client.force_login(self.owner)
        delete_url = reverse(
            "courses:class_delete", args=[self.course.pk, self.class_group.pk]
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(
            csrf_client.post(delete_url, {"confirmation": "delete"}).status_code,
            403,
        )
