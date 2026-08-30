from types import SimpleNamespace
from unittest.mock import patch

from django.core.exceptions import ImproperlyConfigured
from django.test import SimpleTestCase

from apps.api.rollout import is_vue_module_enabled


class VueModuleRolloutTests(SimpleTestCase):
    prefix = "VUE_TEACHER_EXPERIMENTS"

    def rollout(self, user=None, course=None, task=None):
        return is_vue_module_enabled(
            "teacher_experiments",
            user=user,
            course=course,
            task=task,
        )

    def env(self, **values):
        defaults = {
            f"{self.prefix}_ENABLED": "0",
            f"{self.prefix}_COURSE_IDS": "",
            f"{self.prefix}_USER_IDS": "",
        }
        defaults.update(values)
        return patch.dict("os.environ", defaults)

    def test_global_switch_off_always_uses_legacy_ui(self):
        with self.env(**{f"{self.prefix}_USER_IDS": "7"}):
            self.assertFalse(self.rollout(user=SimpleNamespace(pk=7, is_authenticated=True)))

    def test_empty_allowlists_enable_every_authenticated_context(self):
        with self.env(**{f"{self.prefix}_ENABLED": "1"}):
            self.assertTrue(self.rollout(user=SimpleNamespace(pk=7, is_authenticated=True)))

    def test_course_or_user_allowlist_can_enable_a_pilot(self):
        values = {
            f"{self.prefix}_ENABLED": "1",
            f"{self.prefix}_COURSE_IDS": "17,42",
            f"{self.prefix}_USER_IDS": "5,8",
        }
        with self.env(**values):
            self.assertTrue(
                self.rollout(
                    user=SimpleNamespace(pk=99, is_authenticated=True),
                    course=SimpleNamespace(pk=42),
                )
            )
            self.assertTrue(self.rollout(user=SimpleNamespace(pk=5, is_authenticated=True)))
            self.assertFalse(
                self.rollout(
                    user=SimpleNamespace(pk=99, is_authenticated=True),
                    course=SimpleNamespace(pk=43),
                )
            )

    def test_task_resolves_its_authoritative_course(self):
        task = SimpleNamespace(class_group=SimpleNamespace(course_id=17))
        with self.env(
            **{
                f"{self.prefix}_ENABLED": "1",
                f"{self.prefix}_COURSE_IDS": "17",
            }
        ):
            self.assertTrue(
                self.rollout(
                    user=SimpleNamespace(pk=99, is_authenticated=True),
                    task=task,
                )
            )

    def test_anonymous_user_never_enters_vue_rollout(self):
        with self.env(**{f"{self.prefix}_ENABLED": "1"}):
            self.assertFalse(self.rollout(user=SimpleNamespace(is_authenticated=False)))

    def test_invalid_allowlist_is_not_silently_ignored(self):
        with self.env(
            **{
                f"{self.prefix}_ENABLED": "1",
                f"{self.prefix}_COURSE_IDS": "17,not-an-id",
            }
        ):
            with self.assertRaises(ImproperlyConfigured):
                self.rollout(user=SimpleNamespace(pk=5, is_authenticated=True))
