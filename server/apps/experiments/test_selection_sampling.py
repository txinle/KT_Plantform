from django.core.exceptions import ValidationError
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase
from django.urls import reverse

from apps.accounts.models import Course, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from apps.assessment.models import ExperimentSession
from apps.assessment.services import get_or_create_session

from .forms import ExperimentForm
from .models import Experiment, ExperimentQuestion, ExperimentQuestionQuota, ExperimentStudent, QuestionSnapshot
from .services import (
    derive_session_seeds,
    ordered_snapshots,
    publish_experiment,
    seed_for,
    select_snapshots,
)


class ExperimentQuotaFormTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="quota-form-teacher",
            role=User.Role.TEACHER,
        )
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="配额题库")

    def payload(self, **overrides):
        data = {
            "name": "配额实验",
            "description": "",
            "question_bank": self.bank.pk,
            "selection_mode": Experiment.SelectionMode.QUOTA,
            "ordering_mode": Experiment.Ordering.FIXED,
            "feedback_mode": Experiment.Feedback.NONE,
            "time_mode": Experiment.TimeMode.NONE,
            "total_time_limit_minutes": "",
            "per_question_time_limit_seconds": "",
            "resume_mode": Experiment.ResumeMode.RESUMABLE,
        }
        data.update({f"quota_{question_type}": 0 for question_type in Question.Type.values})
        data.update(overrides)
        return data

    def test_quota_mode_requires_a_positive_total(self):
        form = ExperimentForm(self.payload(), owner=self.teacher)

        self.assertFalse(form.is_valid())
        self.assertIn("至少需要抽取 1 道题", str(form.errors["selection_mode"]))

    def test_quota_form_persists_a_nonnegative_row_for_every_question_type(self):
        form = ExperimentForm(
            self.payload(quota_single_choice=2, quota_true_false=1),
            owner=self.teacher,
        )

        self.assertTrue(form.is_valid(), form.errors)
        experiment = form.save(commit=False)
        experiment.owner = self.teacher
        experiment.save()
        form.save_quotas(experiment)

        self.assertEqual(
            dict(experiment.question_quotas.values_list("question_type", "amount")),
            {
                Question.Type.SINGLE: 2,
                Question.Type.MULTIPLE: 0,
                Question.Type.NUMERIC: 0,
                Question.Type.SHORT: 0,
                Question.Type.FILL_BLANK: 0,
                Question.Type.TRUE_FALSE: 1,
            },
        )

    def test_legacy_form_payload_without_selection_fields_defaults_to_all_mode(self):
        data = self.payload()
        data.pop("selection_mode")
        for question_type in Question.Type.values:
            data.pop(f"quota_{question_type}")

        form = ExperimentForm(data, owner=self.teacher)

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["selection_mode"], Experiment.SelectionMode.ALL)

    def test_all_mode_edit_preserves_existing_quotas_when_native_js_disables_inputs(self):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="保留配额",
            selection_mode=Experiment.SelectionMode.QUOTA,
        )
        experiment.question_quotas.filter(question_type=Question.Type.SINGLE).update(amount=3)
        data = self.payload(selection_mode=Experiment.SelectionMode.ALL)
        for question_type in Question.Type.values:
            data.pop(f"quota_{question_type}")

        form = ExperimentForm(data, instance=experiment, owner=self.teacher)

        self.assertTrue(form.is_valid(), form.errors)
        updated = form.save(commit=False)
        updated.save()
        form.save_quotas(updated)

        self.assertEqual(
            updated.question_quotas.get(question_type=Question.Type.SINGLE).amount,
            3,
        )

    def test_legacy_create_view_saves_submitted_quota_values(self):
        self.client.force_login(self.teacher)

        response = self.client.post(
            reverse("teacher-standalone-experiment-collection"),
            {**{key: value for key, value in self.payload(quota_single_choice=3, quota_true_false=2).items()
                  if key not in {"question_bank", "total_time_limit_minutes", "per_question_time_limit_seconds"} and not key.startswith("quota_")},
             "question_bank_id": self.bank.pk,
             "question_quotas": [{"question_type": "single_choice", "amount": 3}, {"question_type": "true_false", "amount": 2}]}, content_type="application/json",
        )

        self.assertEqual(response.status_code, 201)
        experiment = Experiment.objects.get(name="配额实验")
        self.assertEqual(experiment.selection_mode, Experiment.SelectionMode.QUOTA)
        self.assertEqual(
            dict(experiment.question_quotas.values_list("question_type", "amount"))[Question.Type.SINGLE],
            3,
        )
        self.assertEqual(
            dict(experiment.question_quotas.values_list("question_type", "amount"))[Question.Type.TRUE_FALSE],
            2,
        )



class QuotaPublishingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="quota-publish-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="quota-publish-student",
            student_number="quota-publish-student",
            role=User.Role.STUDENT,
        )
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="发布配额题库")
        self.concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="发布 KC",
            normalized_name="发布 kc",
        )

    def question(self, sequence, question_type, correct_answer, *, options=None):
        question = Question.objects.create(
            bank=self.bank,
            sequence=sequence,
            question_type=question_type,
            text=f"题目 {sequence}",
            correct_answer=correct_answer,
        )
        QuestionConcept.objects.create(question=question, concept=self.concept, is_primary=True)
        for index, (key, text) in enumerate((options or {}).items(), start=1):
            QuestionOption.objects.create(question=question, key=key, text=text, display_order=index)
        return question

    def experiment(self, **quota_values):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="按配额发布",
            selection_mode=Experiment.SelectionMode.QUOTA,
        )
        for question_type, amount in quota_values.items():
            experiment.question_quotas.filter(question_type=question_type).update(amount=amount)
        ExperimentStudent.objects.create(experiment=experiment, student=self.student)
        return experiment

    def test_quota_publish_freezes_the_full_active_candidate_pool_and_inventory_configuration(self):
        linked = self.question(1, Question.Type.SINGLE, "A", options={"A": "正确", "B": "错误"})
        self.question(2, Question.Type.SINGLE, "A", options={"A": "正确", "B": "错误"})
        self.question(3, Question.Type.SINGLE, "A", options={"A": "正确", "B": "错误"})
        self.question(4, Question.Type.FILL_BLANK, "Knowledge Tracing")
        self.question(5, Question.Type.SHORT, "简答")
        experiment = self.experiment(single_choice=2, fill_blank=1)
        ExperimentQuestion.objects.create(experiment=experiment, question=linked, display_order=1)

        version = publish_experiment(experiment, self.teacher)

        self.assertEqual(QuestionSnapshot.objects.filter(version=version).count(), 5)
        self.assertEqual(version.configuration["selection_mode"], Experiment.SelectionMode.QUOTA)
        self.assertEqual(version.configuration["quota_map"][Question.Type.SINGLE], 2)
        self.assertEqual(version.configuration["quota_map"][Question.Type.FILL_BLANK], 1)
        self.assertEqual(version.configuration["candidate_inventory"][Question.Type.SINGLE], {"need": 2, "available": 3})
        self.assertEqual(version.configuration["candidate_inventory"][Question.Type.FILL_BLANK], {"need": 1, "available": 1})
        self.assertEqual(version.configuration["candidate_count"], 5)
        self.assertEqual(version.configuration["sampling_algorithm_version"], "v1")
        self.assertEqual(len(version.configuration["sampling_salt"]), 64)

    def test_quota_publish_rejects_insufficient_inventory_without_creating_any_snapshot(self):
        linked = self.question(1, Question.Type.FILL_BLANK, "Knowledge Tracing")
        experiment = self.experiment(fill_blank=2)
        ExperimentQuestion.objects.create(experiment=experiment, question=linked, display_order=1)

        with self.assertRaisesMessage(ValidationError, "填空题：需要 2 题，可用 1 题"):
            publish_experiment(experiment, self.teacher)

        experiment.refresh_from_db()
        self.assertEqual(experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(experiment.versions.exists())
        self.assertFalse(QuestionSnapshot.objects.exists())

    def test_quota_publish_rejects_a_zero_total_when_the_form_is_bypassed(self):
        linked = self.question(1, Question.Type.FILL_BLANK, "Knowledge Tracing")
        experiment = self.experiment()
        ExperimentQuestion.objects.create(experiment=experiment, question=linked, display_order=1)

        with self.assertRaisesMessage(ValidationError, "至少需要抽取 1 道题"):
            publish_experiment(experiment, self.teacher)

        experiment.refresh_from_db()
        self.assertEqual(experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(experiment.versions.exists())

    def test_publish_rejects_an_active_question_with_an_invalid_judgement_answer(self):
        linked = self.question(1, Question.Type.TRUE_FALSE, "不确定")
        experiment = self.experiment(true_false=1)
        ExperimentQuestion.objects.create(experiment=experiment, question=linked, display_order=1)

        with self.assertRaisesMessage(ValidationError, "判断题"):
            publish_experiment(experiment, self.teacher)

        self.assertFalse(experiment.versions.exists())

class SamplingSessionTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="sampling-teacher",
            role=User.Role.TEACHER,
        )
        self.students = [
            User.objects.create_user(
                username=f"sampling-student-{index}",
                student_number=f"sampling-student-{index}",
                role=User.Role.STUDENT,
            )
            for index in range(1, 4)
        ]
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="个体抽题题库")
        self.concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="抽题 KC",
            normalized_name="抽题 kc",
        )

    def question(self, sequence, question_type):
        if question_type == Question.Type.SINGLE:
            answer, options = "A", {"A": "正确", "B": "错误"}
        elif question_type == Question.Type.MULTIPLE:
            answer, options = ["A", "B"], {"A": "一", "B": "二", "C": "三"}
        elif question_type == Question.Type.NUMERIC:
            answer, options = "1", None
        elif question_type == Question.Type.TRUE_FALSE:
            answer, options = True, None
        else:
            answer, options = "文本答案", None
        question = Question.objects.create(
            bank=self.bank,
            sequence=sequence,
            question_type=question_type,
            text=f"抽题 {sequence}",
            correct_answer=answer,
        )
        QuestionConcept.objects.create(question=question, concept=self.concept, is_primary=True)
        for order, (key, text) in enumerate((options or {}).items(), start=1):
            QuestionOption.objects.create(question=question, key=key, text=text, display_order=order)
        return question

    def publish(self, *, selection_mode=Experiment.SelectionMode.QUOTA, ordering_mode=Experiment.Ordering.FIXED, quotas=None):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name=f"抽题实验-{ordering_mode}",
            selection_mode=selection_mode,
            ordering_mode=ordering_mode,
        )
        for question_type, amount in (quotas or {}).items():
            experiment.question_quotas.filter(question_type=question_type).update(amount=amount)
        for student in self.students:
            ExperimentStudent.objects.create(experiment=experiment, student=student)
        version = publish_experiment(experiment, self.teacher)
        experiment.refresh_from_db()
        return experiment, version

    def test_quota_session_draws_exact_type_counts_without_replacement_and_keeps_assignments(self):
        for sequence in range(1, 6):
            self.question(sequence, Question.Type.SINGLE)
        for sequence in range(6, 9):
            self.question(sequence, Question.Type.FILL_BLANK)
        experiment, version = self.publish(
            quotas={Question.Type.SINGLE: 3, Question.Type.FILL_BLANK: 2},
        )

        session = get_or_create_session(experiment, self.students[0])
        assignments = list(session.assignments.select_related("snapshot"))
        repeated = get_or_create_session(experiment, self.students[0])

        self.assertEqual(len(assignments), 5)
        self.assertEqual(len({assignment.snapshot_id for assignment in assignments}), 5)
        self.assertEqual(
            [assignment.snapshot.question_type for assignment in assignments].count(Question.Type.SINGLE),
            3,
        )
        self.assertEqual(
            [assignment.snapshot.question_type for assignment in assignments].count(Question.Type.FILL_BLANK),
            2,
        )
        self.assertEqual(repeated.pk, session.pk)
        self.assertEqual(repeated.assignments.count(), 5)
        self.assertEqual(session.sampling_algorithm_version, version.configuration["sampling_algorithm_version"])
        self.assertEqual(len(session.sampling_seed), 64)
        self.assertEqual(len(session.ordering_seed), 64)
        self.assertNotEqual(session.sampling_seed, session.ordering_seed)

    def test_quota_sampling_covers_each_of_the_six_question_types(self):
        for sequence, question_type in enumerate(Question.Type.values, start=1):
            self.question(sequence, question_type)
        experiment, _version = self.publish(
            quotas={question_type: 1 for question_type in Question.Type.values},
        )

        session = get_or_create_session(experiment, self.students[0])
        assigned_types = list(session.assignments.values_list("snapshot__question_type", flat=True))

        self.assertEqual(len(assigned_types), 6)
        self.assertEqual(
            {question_type: assigned_types.count(question_type) for question_type in Question.Type.values},
            {question_type: 1 for question_type in Question.Type.values},
        )

    def test_quota_sampling_and_ordering_are_reproducible_from_independent_seeds(self):
        for sequence in range(1, 10):
            self.question(sequence, Question.Type.SINGLE)
        _experiment, version = self.publish(
            ordering_mode=Experiment.Ordering.RANDOM,
            quotas={Question.Type.SINGLE: 4},
        )
        configuration = dict(version.configuration)
        configuration["sampling_salt"] = "reproducible-seed-salt"
        version.configuration = configuration
        version.save(update_fields=["configuration"])

        sampling_seed, ordering_seed, _algorithm_version = derive_session_seeds(
            version,
            self.students[0].pk,
        )
        first_sample = select_snapshots(version, self.students[0].pk, sampling_seed)
        second_sample = select_snapshots(version, self.students[0].pk, sampling_seed)
        first_order = ordered_snapshots(
            version,
            self.students[0].pk,
            first_sample,
            ordering_seed,
        )
        second_order = ordered_snapshots(
            version,
            self.students[0].pk,
            second_sample,
            ordering_seed,
        )

        self.assertNotEqual(sampling_seed, ordering_seed)
        self.assertEqual(
            [snapshot.pk for snapshot in first_sample],
            [snapshot.pk for snapshot in second_sample],
        )
        self.assertEqual(
            [snapshot.pk for snapshot in first_order],
            [snapshot.pk for snapshot in second_order],
        )

    def test_different_students_receive_different_deterministic_samples_when_the_pool_is_large(self):
        for sequence in range(1, 61):
            self.question(sequence, Question.Type.SINGLE)
        experiment, version = self.publish(quotas={Question.Type.SINGLE: 3})
        configuration = dict(version.configuration)
        configuration["sampling_salt"] = "stable-test-salt"
        version.configuration = configuration
        version.save(update_fields=["configuration"])

        first = get_or_create_session(experiment, self.students[0])
        second = get_or_create_session(experiment, self.students[1])

        first_ids = set(first.assignments.values_list("snapshot_id", flat=True))
        second_ids = set(second.assignments.values_list("snapshot_id", flat=True))
        self.assertNotEqual(first_ids, second_ids)

    def test_fixed_order_sorts_the_drawn_personal_set_by_bank_sequence(self):
        for sequence in range(1, 9):
            self.question(sequence, Question.Type.SINGLE)
        experiment, _version = self.publish(quotas={Question.Type.SINGLE: 4})

        session = get_or_create_session(experiment, self.students[0])

        bank_sequences = list(session.assignments.values_list("snapshot__bank_sequence", flat=True))
        self.assertEqual(bank_sequences, sorted(bank_sequences))

    def test_legacy_all_mode_version_retains_the_previous_ordering_seed(self):
        for sequence in range(1, 4):
            self.question(sequence, Question.Type.SINGLE)
        _experiment, version = self.publish(
            selection_mode=Experiment.SelectionMode.ALL,
            ordering_mode=Experiment.Ordering.RANDOM,
        )
        version.configuration = {"ordering_mode": Experiment.Ordering.RANDOM}
        version.save(update_fields=["configuration"])

        _sampling_seed, ordering_seed, algorithm_version = derive_session_seeds(
            version,
            self.students[0].pk,
        )

        self.assertEqual(
            int(ordering_seed[:16], 16),
            seed_for(version.pk, self.students[0].pk),
        )
        self.assertEqual(algorithm_version, "legacy-v0")

    def test_published_candidate_pool_is_immutable_and_all_mode_assigns_every_snapshot(self):
        for sequence in range(1, 4):
            self.question(sequence, Question.Type.SINGLE)
        experiment, version = self.publish(selection_mode=Experiment.SelectionMode.ALL)
        self.question(4, Question.Type.SINGLE)

        session = get_or_create_session(experiment, self.students[0])

        self.assertEqual(version.question_snapshots.count(), 3)
        self.assertEqual(session.assignments.count(), 3)
        self.assertEqual(session.sampling_algorithm_version, "v1")


class ExperimentQuestionQuotaMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("experiments", "0003_snapshot_course_question_identity")
    migrate_to = ("experiments", "0004_experiment_selection_mode_and_more")

    @staticmethod
    def _targets_with_experiments_at(executor, experiments_target):
        return [
            experiments_target
            if target[0] == "experiments"
            else ("assessment", "0002_experimentsession_sampling_seeds")
            if target[0] == "assessment"
            else ("analytics", "0013_researchsessionmaterialization_source_revision")
            if target[0] == "analytics"
            else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        # Class cleanup also runs if historical fixture creation fails in setUpClass.
        cls.addClassCleanup(cls._restore_schema)
        cls.old_targets = cls._targets_with_experiments_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Experiment = old_apps.get_model("experiments", "Experiment")
        teacher = User.objects.create(username="quota-migration-teacher", role="teacher")
        bank = QuestionBank.objects.create(owner_id=teacher.pk, name="迁移前题库")
        experiment = Experiment.objects.create(
            owner_id=teacher.pk,
            question_bank_id=bank.pk,
            name="迁移前实验",
        )
        cls.experiment_pk = experiment.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls._targets_with_experiments_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def _restore_schema(cls):
        MigrationExecutor(connection).migrate(cls.current_targets)

    def test_existing_experiments_receive_all_mode_and_six_zero_quota_rows(self):
        Experiment = self.apps.get_model("experiments", "Experiment")
        ExperimentQuestionQuota = self.apps.get_model("experiments", "ExperimentQuestionQuota")

        experiment = Experiment.objects.get(pk=self.experiment_pk)
        rows = dict(
            ExperimentQuestionQuota.objects.filter(experiment_id=experiment.pk).values_list(
                "question_type", "amount"
            )
        )

        self.assertEqual(experiment.selection_mode, "all")
        self.assertEqual(
            rows,
            {
                "single_choice": 0,
                "multiple_choice": 0,
                "numeric": 0,
                "short_answer": 0,
                "fill_blank": 0,
                "true_false": 0,
            },
        )
