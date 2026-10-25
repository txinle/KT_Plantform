from io import StringIO
from unittest.mock import patch

from django.core.management import CommandError, call_command, get_commands
from django.test import TestCase, override_settings

from .models import User


class AddAdminCommandTests(TestCase):
    password = "NewAdmin-Strong-4729!"

    def run_command(self, username="new-admin", passwords=None):
        output = StringIO()
        with (
            patch("builtins.input", return_value=username),
            patch(
                "apps.accounts.management.commands.add_admin.getpass",
                side_effect=passwords or [self.password, self.password],
            ),
        ):
            call_command("add_admin", stdout=output)
        return output.getvalue()

    def test_creates_active_full_administrator_without_printing_password(self):
        self.assertIn("add_admin", get_commands())
        output = self.run_command()
        user = User.objects.get(username="new-admin")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertTrue(user.is_active and user.is_staff and user.is_superuser)
        self.assertTrue(user.check_password(self.password))
        self.assertNotEqual(user.password, self.password)
        self.assertNotIn(self.password, output)

    def test_duplicate_does_not_change_existing_account(self):
        user = User.objects.create_user(username="new-admin", password="Existing-Password-748!")
        original = (user.password, user.role, user.is_staff, user.is_superuser)
        with self.assertRaisesMessage(CommandError, "已存在"):
            self.run_command()
        user.refresh_from_db()
        self.assertEqual((user.password, user.role, user.is_staff, user.is_superuser), original)

    def test_mismatched_passwords_create_no_account(self):
        with self.assertRaisesMessage(CommandError, "不一致"):
            self.run_command(passwords=[self.password, "Different-Password-839!"])
        self.assertFalse(User.objects.exists())

    def test_invalid_username_creates_no_account(self):
        with self.assertRaises(CommandError):
            self.run_command(username="invalid username")
        self.assertFalse(User.objects.exists())

    @override_settings(AUTH_PASSWORD_VALIDATORS=[
        {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    ])
    def test_weak_password_creates_no_account(self):
        with self.assertRaises(CommandError):
            self.run_command(passwords=["123", "123"])
        self.assertFalse(User.objects.exists())
