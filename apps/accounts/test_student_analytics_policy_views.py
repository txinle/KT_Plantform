from django.test import TransactionTestCase, override_settings
from django.urls import reverse

from apps.audit.models import AuditLog

from .models import ClassGroup, ClassStudentAnalyticsPolicy, Course, User


@override_settings(CLASS_CENTER_ENABLED=True)
class StudentAnalyticsPolicyViewTests(TransactionTestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="policy-view-owner",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="policy-view-collaborator",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="policy-view-outsider",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.owner,
            name="学生显示设置课程",
            code="VISIBILITY-VIEW",
            term="2026 秋",
        )
        self.course.teacher_links.create(teacher=self.collaborator)
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="显示设置班",
            academic_year="2026",
        )
        self.policy = self.class_group.student_analytics_policy
        self.url = reverse(
            "courses:class_student_visibility",
            args=[self.course.pk, self.class_group.pk],
        )

    def test_get_is_read_only_and_class_center_links_to_settings(self):
        self.client.force_login(self.owner)

        response = self.client.get(self.url)
        center = self.client.get(
            reverse(
                "courses:class_detail",
                args=[self.course.pk, self.class_group.pk],
            )
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "teacher/course_class_student_visibility.html")
        self.assertContains(response, "学生端显示设置")
        self.assertContains(response, "完整排行榜不在 v1 范围内")
        self.assertContains(center, self.url)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_post_uses_policy_service_dependencies_and_audit(self):
        self.client.force_login(self.collaborator)

        response = self.client.post(
            self.url,
            {
                "show_scores": "on",
                "show_item_results": "on",
                "show_class_average": "on",
                "rank_mode": ClassStudentAnalyticsPolicy.RankMode.SELF,
            },
        )

        self.assertRedirects(response, self.url)
        self.policy.refresh_from_db()
        self.assertTrue(self.policy.show_scores)
        self.assertFalse(self.policy.show_correct_rate)
        self.assertFalse(self.policy.show_kc_rates)
        self.assertFalse(self.policy.show_trend)
        self.assertEqual(self.policy.rank_mode, ClassStudentAnalyticsPolicy.RankMode.SELF)
        audit = AuditLog.objects.get(
            action="class.student_analytics_policy.update"
        )
        self.assertEqual(audit.actor_id, self.collaborator.pk)

    def test_archived_course_is_read_only_and_post_is_forbidden(self):
        Course.objects.filter(pk=self.course.pk).update(
            status=Course.Status.ARCHIVED
        )
        self.client.force_login(self.owner)

        get_response = self.client.get(self.url)
        post_response = self.client.post(
            self.url,
            {"show_scores": "on", "rank_mode": "none"},
        )

        self.assertEqual(get_response.status_code, 200)
        self.assertContains(get_response, "课程已归档，设置只读")
        self.assertEqual(post_response.status_code, 403)
        self.assertEqual(AuditLog.objects.count(), 0)

    def test_outsider_wrong_pair_and_disabled_flag_fail_closed(self):
        other_course = Course.objects.create(
            owner=self.owner,
            name="其他显示设置课程",
            code="VISIBILITY-OTHER",
            term="2026 秋",
        )
        wrong_url = reverse(
            "courses:class_student_visibility",
            args=[other_course.pk, self.class_group.pk],
        )

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(self.url).status_code, 404)

        self.client.force_login(self.owner)
        self.assertEqual(self.client.get(wrong_url).status_code, 404)
        with override_settings(CLASS_CENTER_ENABLED=False):
            self.assertEqual(self.client.get(self.url).status_code, 404)
