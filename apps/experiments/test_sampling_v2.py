from collections import Counter
import uuid

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Course, User
from apps.assessment.models import (
    Attempt,
    ExperimentSession,
    StudentQuestionAssignment,
    SubmissionEvent,
)
from apps.question_bank.models import Concept, Question, QuestionBank
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping

from .cross_experiment_items import student_item_context
from .models import (
    Experiment,
    ExperimentQuestionQuota,
    ExperimentSamplingKC,
    ExperimentVersion,
    QuestionSnapshot,
)
from .presentation_instances import get_or_create_default_item_instance
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

    def test_exclude_previous_filters_submitted_and_active_assignment_items(self):
        submitted = self.question(1, self.a)
        active = self.question(2, self.a)
        available = self.question(3, self.a)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=1)
        experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.EXCLUDE_PREVIOUS
        experiment.save(update_fields=["cross_experiment_item_policy"])
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)

        selected = SamplingService(
            experiment,
            completed_question_ids={submitted.pk},
            active_frozen_question_ids={active.pk},
        ).select_with_attribution("c" * 64)

        self.assertEqual([row["question"].pk for row in selected], [available.pk])
        self.assertEqual(selected[0]["history_role"], "new")
        self.assertEqual(selected[0]["cross_experiment_item_policy"], "exclude_previous")

    def test_mixed_review_selects_exact_review_count_with_type_quotas(self):
        review_single = self.question(1, self.a, question_type=Question.Type.SINGLE)
        review_multiple = self.question(2, self.a, question_type=Question.Type.MULTIPLE)
        new_single = self.question(3, self.a, question_type=Question.Type.SINGLE)
        new_multiple = self.question(4, self.a, question_type=Question.Type.MULTIPLE)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=2)
        experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
        experiment.review_count = 1
        experiment.save(update_fields=["cross_experiment_item_policy", "review_count"])
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)
        ExperimentQuestionQuota.objects.filter(
            experiment=experiment, question_type=Question.Type.SINGLE,
        ).update(amount=1)
        ExperimentQuestionQuota.objects.filter(
            experiment=experiment, question_type=Question.Type.MULTIPLE,
        ).update(amount=1)

        selected = SamplingService(
            experiment,
            completed_question_ids={review_single.pk, review_multiple.pk},
            active_frozen_question_ids=set(),
        ).select_with_attribution("d" * 64)

        self.assertEqual(Counter(row["history_role"] for row in selected), Counter({"review": 1, "new": 1}))
        self.assertEqual(
            Counter(row["question"].question_type for row in selected),
            Counter({Question.Type.SINGLE: 1, Question.Type.MULTIPLE: 1}),
        )
        self.assertTrue({row["question"].pk for row in selected}.issubset({
            review_single.pk, review_multiple.pk, new_single.pk, new_multiple.pk,
        }))

    def test_mixed_review_reports_review_and_new_pool_shortages(self):
        review = self.question(1, self.a)
        new = self.question(2, self.a)
        experiment = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=3)
        experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
        experiment.review_count = 1
        experiment.save(update_fields=["cross_experiment_item_policy", "review_count"])
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=self.a)

        with self.assertRaises(SamplingValidationError) as raised:
            SamplingService(
                experiment,
                completed_question_ids={review.pk},
                active_frozen_question_ids={review.pk},
            ).resolve()
        self.assertEqual(
            {issue["code"] for issue in raised.exception.issues},
            {"REVIEW_POOL_INSUFFICIENT", "NEW_POOL_INSUFFICIENT"},
        )

    def test_mixed_review_jointly_satisfies_history_and_overlapping_kc_quotas(self):
        review = self.question(1, self.a)
        new = self.question(2, self.a, self.b)
        experiment = self.experiment(Experiment.SamplingMode.KC_QUOTA)
        experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
        experiment.review_count = 1
        experiment.save(update_fields=["cross_experiment_item_policy", "review_count"])
        for concept in (self.a, self.b):
            ExperimentSamplingKC.objects.create(
                experiment=experiment,
                concept=concept,
                quota=1,
                match_scope=Experiment.MatchScope.ANY_LEVEL,
            )

        selected = SamplingService(
            experiment,
            completed_question_ids={review.pk},
        ).select_with_attribution("e" * 64)

        self.assertEqual(
            {(row["question"].pk, row["quota_kc_id"]) for row in selected},
            {(review.pk, self.a.pk), (new.pk, self.b.pk)},
        )

    def test_mixed_review_reports_global_infeasibility_after_local_counts_pass(self):
        review_b1 = self.question(1, self.b)
        review_b2 = self.question(2, self.b)
        self.question(3, self.a)
        self.question(4, self.a)
        experiment = self.experiment(Experiment.SamplingMode.KC_QUOTA)
        experiment.cross_experiment_item_policy = Experiment.CrossExperimentItemPolicy.MIXED_REVIEW
        experiment.review_count = 1
        experiment.save(update_fields=["cross_experiment_item_policy", "review_count"])
        ExperimentSamplingKC.objects.create(
            experiment=experiment,
            concept=self.a,
            quota=1,
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )
        ExperimentSamplingKC.objects.create(
            experiment=experiment,
            concept=self.b,
            quota=2,
            match_scope=Experiment.MatchScope.ANY_LEVEL,
        )

        with self.assertRaises(SamplingValidationError) as raised:
            SamplingService(
                experiment,
                completed_question_ids={review_b1.pk, review_b2.pk},
            ).resolve()

        self.assertEqual(raised.exception.issues[0]["code"], "GLOBAL_MATCHING_INSUFFICIENT")
        self.assertEqual(raised.exception.issues[0]["maximum_assignable"], 2)

    def test_student_item_context_uses_formal_submission_source_question(self):
        student = User.objects.create_user(username="sampling-history-student", role=User.Role.STUDENT)
        prior_question = self.question(10, self.a)
        current = self.experiment(Experiment.SamplingMode.TOTAL_COUNT, total_count=1)
        prior = Experiment.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            question_bank=self.bank,
            name="历史实验",
            cross_experiment_item_policy=Experiment.CrossExperimentItemPolicy.UNRESTRICTED,
        )
        version = ExperimentVersion.objects.create(
            experiment=prior,
            version_number=1,
            published_by=self.teacher,
        )
        snapshot = QuestionSnapshot.objects.create(
            version=version,
            source_question=prior_question,
            source_sequence=prior_question.sequence,
            code=prior_question.code,
            question_type=prior_question.question_type,
            text=prior_question.text,
            correct_answer=prior_question.correct_answer,
            primary_kc=self.a.name,
        )
        session = ExperimentSession.objects.create(version=version, student=student)
        assignment = StudentQuestionAssignment.objects.create(
            session=session,
            snapshot=snapshot,
            item_instance=get_or_create_default_item_instance(snapshot),
            assigned_sequence_index=1,
        )
        attempt = Attempt.objects.create(session=session, assignment=assignment)
        SubmissionEvent.objects.create(
            attempt=attempt,
            submission_token=uuid.uuid4(),
            final_answer="",
            server_received_at=timezone.now(),
            committed_at=timezone.now(),
        )

        context = student_item_context(current, student)

        self.assertEqual(context.completed_question_ids, frozenset({prior_question.pk}))
        self.assertEqual(context.active_frozen_question_ids, frozenset())
        self.assertEqual(context.unresolved_task_ids, ())
