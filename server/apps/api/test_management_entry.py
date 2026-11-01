from django.test import SimpleTestCase
from django.urls import reverse
from apps.accounts.account_queries import resolve_home_url
from apps.accounts.models import User


class ManagementEntryTests(SimpleTestCase):
    def test_platform_admin_enters_business_workspace_and_technical_admin_is_retained(self):
        self.assertEqual(resolve_home_url(User(role=User.Role.ADMIN)), '/manage/')
        self.assertEqual(resolve_home_url(User(role=User.Role.TEACHER, is_superuser=True)), '/manage/')
        self.assertEqual(reverse('admin:index'), '/admin/')

    def test_teacher_entry_stays_in_course_workspace(self):
        self.assertEqual(resolve_home_url(User(role=User.Role.TEACHER)), '/teacher/courses/')
