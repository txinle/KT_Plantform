from apps.api.test_support import domain_payload
from django.test import Client, TestCase
from django.urls import reverse
from uuid import uuid4

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

    def _confirmed_delete(self, url):
        token = domain_payload(self.client.get(url))["confirmation_token"]
        operation_id = str(uuid4())
        registered = self.client.post(
            f"/api/v1/teacher/courses/{self.course.pk}/deletion-operations/",
            {"operation_id": operation_id, "action": "delete_class",
             "object_id": self.class_group.pk, "class_id": self.class_group.pk,
             "confirmation_token": token},
        )
        self.assertEqual(registered.status_code, 201)
        return self.client.post(url, {"operation_id": operation_id,
                                     "confirmation": "delete", "confirmation_token": token})

    def test_delete_requires_explicit_second_confirmation(self):
        self.client.force_login(self.owner)
        url = reverse("teacher-course-class-delete", args=[self.course.pk, self.class_group.pk])

        response = self.client.post(url)

        self.assertEqual(response.status_code, 400)
        self.assertTrue(ClassGroup.objects.filter(pk=self.class_group.pk).exists())

    def test_collaborator_can_delete_empty_class_after_confirmation(self):
        self.client.force_login(self.collaborator)
        url = reverse("teacher-course-class-delete", args=[self.course.pk, self.class_group.pk])

        response = self._confirmed_delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClassGroup.objects.filter(pk=self.class_group.pk).exists())
        self.assertTrue(AuditLog.objects.filter(action="class.delete").exists())

    def test_class_without_experiment_deletes_enrollment_but_preserves_account(self):
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
        url = reverse("teacher-course-class-delete", args=[self.course.pk, self.class_group.pk])

        response = self._confirmed_delete(url)

        self.assertEqual(response.status_code, 200)
        self.assertFalse(ClassGroup.objects.filter(pk=self.class_group.pk).exists())
        self.assertFalse(CourseEnrollment.objects.filter(class_group_id=self.class_group.pk).exists())
        self.assertTrue(User.objects.filter(pk=student.pk).exists())

    def test_class_delete_is_csrf_protected(self):
        self.client.force_login(self.owner)
        delete_url = reverse(
            "teacher-course-class-delete", args=[self.course.pk, self.class_group.pk]
        )

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.owner)
        self.assertEqual(
            csrf_client.post(delete_url, {"confirmation": "delete", "confirmation_token": "unconfirmed"}).status_code,
            403,
        )
