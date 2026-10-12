from django.test import TestCase
from apps.accounts.models import User
from .models import AuditLog


class AuditLogTests(TestCase):
    def test_log_can_store_actor_and_details(self):
        actor = User.objects.create_user(username="auditor", role=User.Role.TEACHER)
        item = AuditLog.objects.create(actor=actor, action="test", object_type="X", object_id="1", details={"ok": True})
        self.assertEqual(item.details["ok"], True)
