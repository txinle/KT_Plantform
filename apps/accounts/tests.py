from django.test import TestCase
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
