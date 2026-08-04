from django.test import TestCase, override_settings
from django.urls import reverse
from .models import User

class HomeRoutingTests(TestCase):
    def test_teacher_is_routed_to_teacher_dashboard(self):
        user = User.objects.create_user(username="t", password="x", role=User.Role.TEACHER)
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("home")), reverse("question_bank:dashboard"))

    def test_student_is_routed_to_student_dashboard(self):
        user = User.objects.create_user(username="S1", password="x", role=User.Role.STUDENT, student_number="S1")
        self.client.force_login(user)
        self.assertRedirects(self.client.get(reverse("home")), reverse("assessment:dashboard"))


class LoginPresentationTests(TestCase):
    @override_settings(DEBUG=True)
    def test_demo_credentials_are_visible_in_debug(self):
        response = self.client.get(reverse("login"))
        self.assertContains(response, "teacher / teacher123")

    @override_settings(DEBUG=False)
    def test_demo_credentials_are_hidden_outside_debug(self):
        response = self.client.get(reverse("login"))
        self.assertNotContains(response, "teacher / teacher123")


class HeaderIdentityTests(TestCase):
    def test_teacher_header_shows_name_and_login_account(self):
        teacher = User.objects.create_user(
            username="teacher-account",
            password="x",
            role=User.Role.TEACHER,
            display_name="陈老师",
        )
        self.client.force_login(teacher)

        response = self.client.get(reverse("question_bank:dashboard"))

        self.assertContains(response, '<span class="account-name">陈老师</span>', html=True)
        self.assertContains(response, '<span class="account-username mono">teacher-account</span>', html=True)
        self.assertNotContains(response, "陈老师 / 教师")

    def test_student_header_uses_the_same_name_and_account_format(self):
        student = User.objects.create_user(
            username="S101",
            password="x",
            role=User.Role.STUDENT,
            student_number="S101",
            display_name="林同学",
        )
        self.client.force_login(student)

        response = self.client.get(reverse("assessment:dashboard"))

        self.assertContains(response, '<span class="account-name">林同学</span>', html=True)
        self.assertContains(response, '<span class="account-username mono">S101</span>', html=True)
        self.assertNotContains(response, "林同学 / 学生")

    def test_header_does_not_repeat_account_when_name_falls_back_to_username(self):
        teacher = User.objects.create_user(
            username="fallback-account",
            password="x",
            role=User.Role.TEACHER,
        )
        self.client.force_login(teacher)

        response = self.client.get(reverse("question_bank:dashboard"))
        account_label = response.content.decode("utf-8").split("account-label", 1)[1].split("</span>", 1)[0]

        self.assertEqual(account_label.count("fallback-account"), 1)
