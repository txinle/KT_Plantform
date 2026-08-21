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

    def test_revision_changes_after_collaborator_mutation_and_page_exposes_refresh_url(self):
        self.client.force_login(self.collaborator)
        revision_url = reverse("courses:revision", args=[self.course.pk])
        before = self.client.get(revision_url).json()["revision"]
        response = self.client.post(
            reverse("courses:class_rename", args=[self.course.pk, self.class_group.pk]),
            {"name": "同步二班"},
        )
        self.assertEqual(response.status_code, 302)
        after = self.client.get(revision_url).json()["revision"]

        self.assertNotEqual(before, after)
        page = self.client.get(reverse("courses:classes", args=[self.course.pk]))
        self.assertContains(page, f'data-course-revision-url="{revision_url}"')
        self.assertContains(page, f'data-course-revision="{after}"')

    def test_javascript_polls_without_cache_and_preserves_dirty_forms(self):
        script = (Path(settings.BASE_DIR) / "static" / "js" / "app.js").read_text(
            encoding="utf-8"
        )

        self.assertIn("data-course-revision-url", script)
        self.assertIn("cache: 'no-store'", script)
        self.assertIn("coursePageDirty", script)
        self.assertIn("window.location.reload()", script)

    def test_stale_course_settings_cannot_overwrite_another_manager(self):
        self.client.force_login(self.collaborator)
        settings_url = reverse("courses:settings", args=[self.course.pk])
        state_token = self.client.get(settings_url).context["form"].initial[
            "state_token"
        ]
        Course.objects.filter(pk=self.course.pk).update(description="负责人刚刚更新")

        response = self.client.post(
            settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "协作教师的陈旧内容",
                "teachers": [self.collaborator.pk],
                "state_token": state_token,
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "课程设置已被其他管理者更新")
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "负责人刚刚更新")
