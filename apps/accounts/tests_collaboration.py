from pathlib import Path

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from .models import ClassGroup, Course, CourseTeacher, User


class CourseCollaborationRefreshTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="refresh-owner", password="password123", role=User.Role.TEACHER
        )
        self.collaborator = User.objects.create_user(
            username="refresh-collaborator", password="password123", role=User.Role.TEACHER
        )
        self.outsider = User.objects.create_user(
            username="refresh-outsider", password="password123", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            owner=self.owner, name="同步课程", code="SYNC", term="2026春"
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)
        self.class_group = ClassGroup.objects.create(
            course=self.course, owner=self.owner, name="同步一班", academic_year="2026"
        )

    def test_owner_and_collaborator_receive_same_revision_and_outsider_gets_404(self):
        url = reverse("courses:revision", args=[self.course.pk])
        revisions = []
        for user in (self.owner, self.collaborator):
            self.client.force_login(user)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response["Cache-Control"], "no-store")
            revisions.append(response.json()["revision"])
        self.assertEqual(revisions[0], revisions[1])

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_base_navigation_drops_removed_route_reference(self):
        template = (Path(settings.BASE_DIR) / "templates" / "base.html").read_text(
            encoding="utf-8"
        )
        removed_route_name = "_".join(("class", "rename"))

        self.assertNotIn(removed_route_name, template)
