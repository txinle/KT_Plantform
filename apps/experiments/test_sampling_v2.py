from collections import Counter

from django.test import TestCase

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, Question, QuestionBank
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping

from .models import Experiment, ExperimentQuestionQuota, ExperimentSamplingKC
from .sampling import SamplingService, SamplingValidationError


class SamplingV2Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="sampling-v2-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="抽题 v2 课程",
            code="SAMPLING-V2",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            name="抽题 v2 题库",
        )
        self.a = Concept.objects.create(course=self.course, code="KC1", name="A")
        self.b = Concept.objects.create(course=self.course, code="KC2", name="B")

    def question(self, sequence, root, child=None, *, question_type=Question.Type.SHORT):
        question = Question.objects.create(
            bank=self.bank,
            sequence=sequence,
            question_type=question_type,
            text=f"题目 {sequence}",
            correct_answer="答案",
            status=Question.Status.DRAFT,
        )
        nodes = [{"kc_code": root.code, "parent_kc_code": None, "order": 1}]
        if child:
            nodes.append({"kc_code": child.code, "parent_kc_code": root.code, "order": 1})
        replace_question_kc_mapping(question, root_kc_code=root.code, nodes=nodes)
        question.status = Question.Status.ACTIVE
        question.save(update_fields=["status"])
        return question

    def experiment(self, mode, total_count=None):
        return Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=self.bank,
            name="抽题 v2",
            sampling_mode=mode,
            sampling_total_count=total_count,
            sampling_match_scope=Experiment.MatchScope.ANY_LEVEL,
        )

    def test_total_count_switches_between_root_and_any_level(self):
        root_a = self.question(1, self.a)
        child_a = self.question(2, self.b, self.a)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=1)
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)

        any_level = SamplingService(experiment).resolve()
        self.assertEqual([item.pk for item in any_level.candidates], [root_a.pk, child_a.pk])

        experiment.sampling_match_scope = Experiment.MatchScope.PRIMARY
        experiment.save(update_fields=["sampling_match_scope"])
        primary_only = SamplingService(experiment).resolve()
        self.assertEqual([item.pk for item in primary_only.candidates], [root_a.pk])

    def test_quota_matching_is_global_deterministic_and_has_unique_questions(self):
        q1 = self.question(1, self.a, self.b)
        q2 = self.question(2, self.a)
        q3 = self.question(3, self.b)
        experiment = self.experiment(Experiment.SamplingMode.KC_QUOTA)
        ExperimentSamplingKC.objects.create(
            experiment=experiment,
            concept=self.a,
            quota=2,
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )
        ExperimentSamplingKC.objects.create(
            experiment=experiment,
            concept=self.b,
            quota=1,
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )

        first = SamplingService(experiment).select_with_attribution("a" * 64)
        second = SamplingService(experiment).select_with_attribution("a" * 64)

        self.assertEqual(first, second)
        self.assertEqual({item["question"].pk for item in first}, {q1.pk, q2.pk, q3.pk})
        self.assertEqual(len(first), 3)
        self.assertEqual(len({item["question"].pk for item in first}), 3)

    def test_quota_preflight_reports_global_overlap_infeasibility(self):
        self.question(1, self.a, self.b)
        self.question(2, self.a, self.b)
        experiment = self.experiment(Experiment.SamplingMode.KC_QUOTA)
        for concept in (self.a, self.b):
            ExperimentSamplingKC.objects.create(
                experiment=experiment,
                concept=concept,
                quota=2,
                match_scope=Experiment.MatchScope.ANY_LEVEL,
            )

        with self.assertRaises(SamplingValidationError) as raised:
            SamplingService(experiment).resolve()

        self.assertEqual(raised.exception.issues[0]["code"], "KC_QUOTA_GLOBAL_INSUFFICIENT")
        self.assertEqual(raised.exception.issues[0]["requested"], 4)
        self.assertEqual(raised.exception.issues[0]["maximum_assignable"], 2)

    def test_total_count_type_quotas_are_exact_deterministic_and_unique(self):
        for sequence in range(1, 5):
            self.question(sequence, self.a, question_type=Question.Type.SINGLE)
        for sequence in range(5, 8):
            self.question(sequence, self.a, question_type=Question.Type.MULTIPLE)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=4)
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)
        ExperimentQuestionQuota.objects.filter(
            experiment=experiment,
            question_type=Question.Type.SINGLE,
        ).update(amount=2)
        ExperimentQuestionQuota.objects.filter(
            experiment=experiment,
            question_type=Question.Type.MULTIPLE,
        ).update(amount=2)

        first = SamplingService(experiment).select_with_attribution("b" * 64)
        second = SamplingService(experiment).select_with_attribution("b" * 64)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 4)
        self.assertEqual(len({item["question"].pk for item in first}), 4)
        self.assertEqual(
            Counter(item["question"].question_type for item in first),
            Counter({Question.Type.SINGLE: 2, Question.Type.MULTIPLE: 2}),
        )
        self.assertEqual(
            Counter(item["quota_question_type"] for item in first),
            Counter({Question.Type.SINGLE: 2, Question.Type.MULTIPLE: 2}),
        )

    def test_total_count_type_quota_preflight_reports_shortage(self):
        self.question(1, self.a, question_type=Question.Type.SINGLE)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=2)
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)
        ExperimentQuestionQuota.objects.filter(
            experiment=experiment,
            question_type=Question.Type.SINGLE,
        ).update(amount=2)

        with self.assertRaises(SamplingValidationError) as raised:
            SamplingService(experiment).resolve()

        self.assertEqual(
            raised.exception.issues[0]["code"],
            "QUESTION_TYPE_QUOTA_INSUFFICIENT",
        )
        self.assertEqual(raised.exception.issues[0]["question_type"], Question.Type.SINGLE)
        self.assertEqual(raised.exception.issues[0]["requested"], 2)
        self.assertEqual(raised.exception.issues[0]["available"], 1)
