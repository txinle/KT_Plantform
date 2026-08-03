from django.test import TestCase
from django.urls import reverse
from apps.accounts.models import User
from apps.experiments.models import Experiment
from apps.question_bank.models import QuestionBank

class ExportPermissionTests(TestCase):
    def test_other_teacher_cannot_export_experiment(self):
        owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        other = User.objects.create_user(username="other", role=User.Role.TEACHER)
        bank = QuestionBank.objects.create(owner=owner, name="bank")
        experiment = Experiment.objects.create(owner=owner, question_bank=bank, name="exp")
        self.client.force_login(other)
        self.assertEqual(self.client.get(reverse("analytics:kt_export", args=[experiment.id])).status_code, 404)
