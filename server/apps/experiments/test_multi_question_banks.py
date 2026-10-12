from django.test import TestCase
from django.apps import apps
from django.db import connection, IntegrityError, transaction
from importlib import import_module
from types import SimpleNamespace

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import Experiment, ExperimentQuestionBank
from .sampling import SamplingService


class MultiQuestionBankTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="multi-bank-teacher", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            owner=self.teacher, name="多题库课程", code="MULTI-BANK", term="2026 秋"
        )
        self.bank_a = QuestionBank.objects.create(
            owner=self.teacher, course=self.course, name="第一章"
        )
        self.bank_b = QuestionBank.objects.create(
            owner=self.teacher, course=self.course, name="第二章"
        )

    def test_experiment_can_attach_multiple_question_banks_in_order(self):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=self.bank_a,
            name="合并抽题",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentQuestionBank.objects.create(
            experiment=experiment, question_bank=self.bank_b, selection_order=2
        )

        self.assertEqual(
            list(experiment.question_bank_links.values_list("question_bank_id", flat=True)),
            [self.bank_a.pk, self.bank_b.pk],
        )
        self.assertEqual(experiment.selected_question_bank_ids, [self.bank_a.pk, self.bank_b.pk])

    def test_sampling_candidates_accept_multiple_banks(self):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=self.bank_a,
            name="合并抽题",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentQuestionBank.objects.create(
            experiment=experiment, question_bank=self.bank_b, selection_order=2
        )
        Question.objects.create(
            bank=self.bank_a, code="A1", text="A", correct_answer="A", question_type="short_answer", status="active", sequence=1
        )
        Question.objects.create(
            bank=self.bank_b, code="B1", text="B", correct_answer="B", question_type="short_answer", status="active", sequence=1
        )

        concept = Concept.objects.create(course=self.course, code="KC1", name="概念")
        for question in Question.objects.filter(bank__in=[self.bank_a, self.bank_b]):
            QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
            question.status = "active"
            question.save()
        candidates = SamplingService.total_count_candidates(
            question_bank_ids=experiment.selected_question_bank_ids,
            concept_ids=[],
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )

        self.assertEqual({question.code for question in candidates}, {"A1", "B1"})

    def test_backfill_preserves_legacy_course_bank_and_excludes_standalone(self):
        course_experiment = Experiment.objects.create(
            owner=self.teacher, course=self.course, question_bank=self.bank_a, name="迁移前课程实验"
        )
        course_experiment.question_bank_links.all().delete()
        standalone = Experiment.objects.create(owner=self.teacher, name="独立实验")
        migration = import_module("apps.experiments.migrations.0031_experiment_question_banks")
        migration.backfill_question_banks(apps, SimpleNamespace(connection=connection))
        self.assertEqual(course_experiment.selected_question_bank_ids, [self.bank_a.pk])
        self.assertFalse(standalone.question_bank_links.exists())
        with self.assertRaises(IntegrityError), transaction.atomic():
            ExperimentQuestionBank.objects.create(
                experiment=course_experiment, question_bank=self.bank_a, selection_order=1
            )
