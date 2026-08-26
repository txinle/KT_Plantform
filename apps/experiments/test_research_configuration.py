from decimal import Decimal

from django.apps import apps as django_apps
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)

from .forms import ExperimentForm
from .models import (
    Experiment,
    ExperimentQuestion,
    ExperimentStudent,
    ExperimentVersion,
    QuestionSnapshot,
)
from .services import publish_experiment


def item_instance_model():
    try:
        return django_apps.get_model("experiments", "ItemInstance")
    except LookupError:
        return None


class ResearchExperimentConfigurationTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="research-config-teacher",
            role=User.Role.TEACHER,
        )
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="研究配置题库",
        )

    def test_research_collection_configuration_has_safe_defaults(self):
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="研究配置实验",
        )

        self.assertTrue(hasattr(experiment, "confidence_mode"))
        self.assertEqual(experiment.confidence_mode, "off")
        self.assertEqual(experiment.confidence_sampling_unit, "attempt")
        self.assertIsNone(experiment.confidence_probability)
        self.assertEqual(experiment.confidence_assignment_version, "v1")
        self.assertEqual(experiment.confidence_scale_version, "v1")
        self.assertEqual(experiment.confidence_prompt_version, "v1")
        self.assertEqual(experiment.heartbeat_interval_seconds, 30)

    def test_sampled_confidence_requires_a_probability(self):
        experiment = Experiment(
            owner=self.teacher,
            question_bank=self.bank,
            name="抽样信心实验",
        )
        experiment.confidence_mode = "sampled"
        experiment.confidence_probability = None

        with self.assertRaises(ValidationError) as raised:
            experiment.full_clean()

        self.assertIn("confidence_probability", raised.exception.message_dict)

    def test_non_sampled_confidence_rejects_a_stale_probability(self):
        experiment = Experiment(
            owner=self.teacher,
            question_bank=self.bank,
            name="关闭信心实验",
        )
        experiment.confidence_mode = "off"
        experiment.confidence_probability = Decimal("0.50")

        with self.assertRaises(ValidationError) as raised:
            experiment.full_clean()

        self.assertIn("confidence_probability", raised.exception.message_dict)

    def test_database_rejects_uninterpretable_confidence_mode_probability_pairs(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Experiment.objects.create(
                owner=self.teacher,
                question_bank=self.bank,
                name="缺少抽样概率",
                confidence_mode="sampled",
                confidence_probability=None,
            )
        with self.assertRaises(IntegrityError), transaction.atomic():
            Experiment.objects.create(
                owner=self.teacher,
                question_bank=self.bank,
                name="关闭模式残留概率",
                confidence_mode="off",
                confidence_probability=Decimal("0.50"),
            )

    def test_heartbeat_interval_and_probability_boundaries_are_validated(self):
        experiment = Experiment(
            owner=self.teacher,
            question_bank=self.bank,
            name="边界实验",
        )
        experiment.confidence_mode = "sampled"
        experiment.confidence_probability = Decimal("1.01")
        experiment.heartbeat_interval_seconds = 14

        with self.assertRaises(ValidationError) as raised:
            experiment.full_clean()

        self.assertIn("confidence_probability", raised.exception.message_dict)
        self.assertIn("heartbeat_interval_seconds", raised.exception.message_dict)

    def test_experiment_form_exposes_research_configuration(self):
        form = ExperimentForm(owner=self.teacher)

        for field_name in (
            "confidence_mode",
            "confidence_sampling_unit",
            "confidence_probability",
            "confidence_randomization_seed",
            "confidence_assignment_version",
            "confidence_scale_version",
            "confidence_prompt_version",
            "heartbeat_interval_seconds",
        ):
            self.assertIn(field_name, form.fields)

    def test_teacher_form_renders_research_collection_as_a_distinct_section(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("experiments:create"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "研究过程采集")
        self.assertContains(response, 'name="confidence_mode"')
        self.assertContains(response, 'name="confidence_sampling_unit"')
        self.assertContains(response, 'name="confidence_probability"')
        self.assertContains(response, 'name="confidence_randomization_seed"')
        self.assertContains(response, 'name="confidence_assignment_version"')
        self.assertContains(response, 'name="confidence_scale_version"')
        self.assertContains(response, 'name="confidence_prompt_version"')
        self.assertContains(response, 'name="heartbeat_interval_seconds"')


class ResearchConfigurationPublishingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="research-publish-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="RESEARCH-PUBLISH-S1",
            student_number="RESEARCH-PUBLISH-S1",
            role=User.Role.STUDENT,
        )
        self.bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="研究发布题库",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="选择 A",
            correct_answer="A",
        )
        QuestionOption.objects.create(
            question=self.question,
            key="A",
            text="A",
            display_order=1,
        )
        QuestionOption.objects.create(
            question=self.question,
            key="B",
            text="B",
            display_order=2,
        )
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="研究 KC",
            normalized_name="研究 kc",
        )
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="研究发布实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.student,
        )

    def test_publish_freezes_confidence_and_heartbeat_configuration(self):
        for field_name, value in {
            "confidence_mode": "sampled",
            "confidence_sampling_unit": "session",
            "confidence_probability": Decimal("0.40"),
            "confidence_randomization_seed": "fixed-seed",
            "confidence_assignment_version": "assign-v2",
            "confidence_scale_version": "scale-v3",
            "confidence_prompt_version": "prompt-v4",
            "heartbeat_interval_seconds": 45,
        }.items():
            self.assertTrue(hasattr(self.experiment, field_name), field_name)
            setattr(self.experiment, field_name, value)
        self.experiment.save()

        version = publish_experiment(self.experiment, self.teacher)

        self.assertEqual(
            {
                key: version.configuration[key]
                for key in (
                    "confidence_mode",
                    "confidence_sampling_unit",
                    "confidence_probability",
                    "randomization_seed",
                    "assignment_version",
                    "confidence_scale_version",
                    "confidence_prompt_version",
                    "heartbeat_interval_seconds",
                )
            },
            {
                "confidence_mode": "sampled",
                "confidence_sampling_unit": "session",
                "confidence_probability": "0.4",
                "randomization_seed": "fixed-seed",
                "assignment_version": "assign-v2",
                "confidence_scale_version": "scale-v3",
                "confidence_prompt_version": "prompt-v4",
                "heartbeat_interval_seconds": 45,
            },
        )
        Experiment.objects.filter(pk=self.experiment.pk).update(
            confidence_mode="off",
            confidence_probability=None,
            heartbeat_interval_seconds=90,
        )
        version.refresh_from_db()
        self.assertEqual(version.configuration["confidence_mode"], "sampled")
        self.assertEqual(version.configuration["heartbeat_interval_seconds"], 45)


class ItemInstanceContractTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="item-instance-teacher",
            role=User.Role.TEACHER,
        )
        self.bank = QuestionBank.objects.create(owner=self.teacher, name="呈现题库")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SINGLE,
            text="呈现题目",
            correct_answer="A",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=self.bank,
            name="呈现实验",
        )
        self.version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        self.snapshot = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=self.question,
            source_sequence=1,
            code="Q1",
            question_type=Question.Type.SINGLE,
            text="呈现题目",
            options=[{"key": "A", "text": "甲"}, {"key": "B", "text": "乙"}],
            correct_answer="A",
            primary_kc="呈现 KC",
            all_kcs=["呈现 KC"],
        )

    def test_item_instance_model_has_reproducible_presentation_fields(self):
        ItemInstance = item_instance_model()
        self.assertIsNotNone(ItemInstance)

        from .presentation_instances import get_or_create_default_item_instance

        instance = get_or_create_default_item_instance(self.snapshot)
        repeated = get_or_create_default_item_instance(self.snapshot)

        self.assertEqual(instance.pk, repeated.pk)
        self.assertEqual(instance.presentation_variant, "default")
        self.assertEqual(instance.option_order, ["A", "B"])
        self.assertEqual(instance.parameter_values, {})
        self.assertEqual(instance.random_seed, "")
        self.assertRegex(instance.presentation_hash, r"^[0-9a-f]{64}$")

    def test_presentation_hash_changes_when_the_visible_configuration_changes(self):
        self.assertIsNotNone(item_instance_model())
        from .presentation_instances import canonical_presentation_hash

        first = canonical_presentation_hash(
            presentation_variant="default",
            option_order=["A", "B"],
            parameter_values={},
            random_seed="",
        )
        reordered = canonical_presentation_hash(
            presentation_variant="default",
            option_order=["B", "A"],
            parameter_values={},
            random_seed="",
        )

        self.assertNotEqual(first, reordered)

    def test_item_instance_rejects_a_claimed_hash_or_non_permutation(self):
        ItemInstance = item_instance_model()
        self.assertIsNotNone(ItemInstance)
        from .presentation_instances import (
            canonical_presentation_hash,
            get_or_create_default_item_instance,
        )

        with self.assertRaises(ValidationError):
            ItemInstance.objects.create(
                question_snapshot=self.snapshot,
                presentation_variant="default",
                option_order=["A", "B"],
                parameter_values={},
                random_seed="",
                presentation_hash="0" * 64,
            )

        invalid_order = ["A", "A"]
        with self.assertRaises(ValidationError):
            ItemInstance.objects.create(
                question_snapshot=self.snapshot,
                presentation_variant="default",
                option_order=invalid_order,
                parameter_values={},
                random_seed="",
                presentation_hash=canonical_presentation_hash(
                    presentation_variant="default",
                    option_order=invalid_order,
                    parameter_values={},
                    random_seed="",
                ),
            )

        valid = get_or_create_default_item_instance(self.snapshot)
        with self.assertRaises(ValidationError):
            ItemInstance.objects.filter(pk=valid.pk).update(
                option_order=["B", "A"]
            )

    def test_true_false_default_instance_freezes_the_visible_option_order(self):
        judgement = QuestionSnapshot.objects.create(
            version=self.version,
            source_question=self.question,
            source_sequence=2,
            code="TF1",
            question_type=Question.Type.TRUE_FALSE,
            text="判断题",
            options=[],
            correct_answer=True,
            primary_kc="呈现 KC",
            all_kcs=["呈现 KC"],
        )
        from .presentation_instances import get_or_create_default_item_instance

        instance = get_or_create_default_item_instance(judgement)

        self.assertEqual(instance.option_order, ["true", "false"])
