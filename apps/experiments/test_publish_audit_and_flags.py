import json
import os
import runpy
from pathlib import Path
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.audit.models import AuditLog
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import (
    allocate_course_question_identity,
)

from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentQuestion,
    ExperimentStudent,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    QuestionSnapshot,
)
from .services import (
    _class_experiment_task_publication_scope,
    publish_experiment,
    save_participant_rules,
)


ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = ROOT / "config" / "settings.py"


class FeatureFlagSettingsTests(SimpleTestCase):
    def _load_flags(self, environment):
        isolated_environment = dict(os.environ)
        for name in (
            "CLASS_CENTER_ENABLED",
            "STUDENT_LEARNING_ANALYTICS_ENABLED",
        ):
            isolated_environment.pop(name, None)
        isolated_environment.update(environment)
        with patch.dict(os.environ, isolated_environment, clear=True):
            settings_globals = runpy.run_path(str(SETTINGS_PATH))
        return (
            settings_globals["CLASS_CENTER_ENABLED"],
            settings_globals["STUDENT_LEARNING_ANALYTICS_ENABLED"],
        )

    def test_flags_are_off_by_default_and_require_both_exact_opt_ins(self):
        cases = (
            ({}, (False, False)),
            (
                {
                    "CLASS_CENTER_ENABLED": "0",
                    "STUDENT_LEARNING_ANALYTICS_ENABLED": "0",
                },
                (False, False),
            ),
            (
                {
                    "CLASS_CENTER_ENABLED": "0",
                    "STUDENT_LEARNING_ANALYTICS_ENABLED": "1",
                },
                (False, False),
            ),
            (
                {
                    "CLASS_CENTER_ENABLED": "1",
                    "STUDENT_LEARNING_ANALYTICS_ENABLED": "0",
                },
                (True, False),
            ),
            (
                {
                    "CLASS_CENTER_ENABLED": "1",
                    "STUDENT_LEARNING_ANALYTICS_ENABLED": "1",
                },
                (True, True),
            ),
        )

        for environment, expected in cases:
            with self.subTest(environment=environment):
                self.assertEqual(self._load_flags(environment), expected)

    def test_flags_reject_non_exact_truthy_values(self):
        self.assertEqual(
            self._load_flags(
                {
                    "CLASS_CENTER_ENABLED": "true",
                    "STUDENT_LEARNING_ANALYTICS_ENABLED": "yes",
                }
            ),
            (False, False),
        )


class PublishAuditTests(TestCase):
    publish_action = "experiment.publish"
    top_level_keys = {
        "course_id",
        "experiment_id",
        "release_snapshot_id",
        "class_tasks",
    }
    class_task_keys = {
        "course_id",
        "class_group_id",
        "experiment_id",
        "task_id",
        "release_snapshot_id",
        "class_snapshot_id",
    }

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="publish-audit-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="SENSITIVE-COURSE-NAME",
            code="SENSITIVE-COURSE-CODE",
            term="SENSITIVE-TERM",
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            name="SENSITIVE-CLASS-A",
            academic_year="2023",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            name="SENSITIVE-CLASS-B",
            academic_year="2024",
        )
        self.student_a = self._create_student(
            "SENSITIVE-STUDENT-A",
            self.class_a,
        )
        self.student_b = self._create_student(
            "SENSITIVE-STUDENT-B",
            self.class_b,
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="SENSITIVE-BANK-NAME",
        )
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="SENSITIVE-QUESTION-TEXT",
            correct_answer="SENSITIVE-CORRECT-ANSWER",
        )
        allocate_course_question_identity(self.course, self.question)
        concept = Concept.objects.create(
            course=self.course,
            name="SENSITIVE-KC-NAME",
        )
        QuestionConcept.objects.create(
            question=self.question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="SENSITIVE-EXPERIMENT-NAME",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=self.question,
            display_order=1,
        )

    def _create_student(self, student_number, class_group):
        student = User.objects.create_user(
            username=student_number,
            student_number=student_number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def _configure_course_roster(self, *classes):
        save_participant_rules(self.experiment, list(classes), [], [])

    def _publish_audits(self, experiment):
        return AuditLog.objects.filter(
            action=self.publish_action,
            object_type="Experiment",
            object_id=str(experiment.pk),
        )

    def _assert_publish_audit(self, experiment, version, tasks, *, course_id):
        audits = self._publish_audits(experiment)
        self.assertEqual(audits.count(), 1)
        audit = audits.get()
        self.assertEqual(audit.actor_id, self.teacher.pk)
        self.assertEqual(set(audit.details), self.top_level_keys)

        expected_tasks = [
            {
                "course_id": task.class_group.course_id,
                "class_group_id": task.class_group_id,
                "experiment_id": task.experiment_id,
                "task_id": task.pk,
                "release_snapshot_id": task.release_snapshot_id,
                "class_snapshot_id": str(task.class_snapshot_id),
            }
            for task in sorted(tasks, key=lambda task: (task.class_group_id, task.pk))
        ]
        self.assertEqual(
            audit.details,
            {
                "course_id": course_id,
                "experiment_id": experiment.pk,
                "release_snapshot_id": version.pk,
                "class_tasks": expected_tasks,
            },
        )
        self.assertEqual(
            audit.details["class_tasks"],
            sorted(
                audit.details["class_tasks"],
                key=lambda row: (row["class_group_id"], row["task_id"]),
            ),
        )
        for row in audit.details["class_tasks"]:
            self.assertEqual(set(row), self.class_task_keys)

        return audit

    def _walk_keys(self, value):
        if isinstance(value, dict):
            for key, child in value.items():
                yield key
                yield from self._walk_keys(child)
        elif isinstance(value, list):
            for child in value:
                yield from self._walk_keys(child)

    def test_direct_course_publish_creates_one_compact_sorted_audit(self):
        self._configure_course_roster(self.class_b, self.class_a)

        version = publish_experiment(self.experiment, self.teacher)

        tasks = list(
            ClassExperimentTask.objects.filter(experiment=self.experiment).select_related(
                "class_group"
            )
        )
        self.assertEqual(len(tasks), 2)
        audit = self._assert_publish_audit(
            self.experiment,
            version,
            tasks,
            course_id=self.course.pk,
        )

        payload = json.dumps(audit.details, ensure_ascii=False, sort_keys=True)
        for sensitive_value in (
            self.course.name,
            self.course.code,
            self.course.term,
            self.class_a.name,
            self.class_b.name,
            self.experiment.name,
            self.question.text,
            self.question.correct_answer,
            self.student_a.username,
            self.student_a.student_number,
            self.student_b.username,
            self.student_b.student_number,
            version.configuration["sampling_salt"],
        ):
            self.assertNotIn(sensitive_value, payload)
        for forbidden_key in (
            "name",
            "student_id",
            "student_ids",
            "student_number",
            "participants",
            "question",
            "answer",
            "configuration",
            "sampling_salt",
            "raw_payload",
        ):
            self.assertNotIn(forbidden_key, set(self._walk_keys(audit.details)))

    def test_direct_legacy_publish_records_an_empty_class_task_list(self):
        legacy_experiment = self._create_legacy_experiment()

        version = publish_experiment(legacy_experiment, self.teacher)

        self._assert_publish_audit(
            legacy_experiment,
            version,
            [],
            course_id=None,
        )
        self.assertFalse(
            ClassExperimentTask.objects.filter(experiment=legacy_experiment).exists()
        )

    def test_course_publish_view_uses_only_the_service_audit_and_exposes_no_audit_payload(self):
        self._configure_course_roster(self.class_a)
        self.client.force_login(self.teacher)
        publish_url = reverse(
            "courses:experiment_publish",
            args=[self.course.pk, self.experiment.pk],
        )
        detail_url = reverse(
            "courses:experiment_detail",
            args=[self.course.pk, self.experiment.pk],
        )

        response = self.client.post(publish_url)

        self.assertRedirects(response, detail_url)
        version = ExperimentVersion.objects.get(experiment=self.experiment)
        tasks = list(
            ClassExperimentTask.objects.filter(experiment=self.experiment).select_related(
                "class_group"
            )
        )
        self._assert_publish_audit(
            self.experiment,
            version,
            tasks,
            course_id=self.course.pk,
        )
        detail_response = self.client.get(detail_url)
        self.assertEqual(detail_response.status_code, 200)
        for internal_audit_field in (
            "release_snapshot_id",
            "class_snapshot_id",
            "class_tasks",
        ):
            self.assertNotContains(detail_response, internal_audit_field)

    def test_legacy_publish_view_uses_only_the_service_audit(self):
        legacy_experiment = self._create_legacy_experiment()
        self.client.force_login(self.teacher)
        publish_url = reverse("experiments:publish", args=[legacy_experiment.pk])

        response = self.client.post(publish_url)

        self.assertRedirects(
            response,
            reverse("experiments:detail", args=[legacy_experiment.pk]),
        )
        version = ExperimentVersion.objects.get(experiment=legacy_experiment)
        self._assert_publish_audit(
            legacy_experiment,
            version,
            [],
            course_id=None,
        )

    def test_audit_create_failure_rolls_back_every_publish_write(self):
        self._configure_course_roster(self.class_a, self.class_b)
        roster_before = list(
            ExperimentStudent.objects.filter(experiment=self.experiment)
            .order_by("student_id")
            .values_list("pk", "student_id", "class_snapshot_name")
        )
        class_rules_before = list(
            self.experiment.participant_class_links.order_by("class_group_id").values_list(
                "class_group_id", flat=True
            )
        )

        with patch.object(AuditLog.objects, "using") as audit_using:
            audit_using.return_value.create.side_effect = RuntimeError(
                "audit storage failure"
            )
            with self.assertRaisesRegex(RuntimeError, "audit storage failure"):
                publish_experiment(self.experiment, self.teacher)

        audit_using.assert_called_once_with("default")
        audit_using.return_value.create.assert_called_once()
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertEqual(
            ExperimentVersion.objects.filter(experiment=self.experiment).count(),
            0,
        )
        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(),
            0,
        )
        self.assertEqual(
            ExperimentClassSnapshot.objects.filter(
                version__experiment=self.experiment
            ).count(),
            0,
        )
        self.assertEqual(
            ExperimentVersionParticipantSnapshot.objects.filter(
                version__experiment=self.experiment
            ).count(),
            0,
        )
        self.assertEqual(
            QuestionSnapshot.objects.filter(version__experiment=self.experiment).count(),
            0,
        )
        self.assertEqual(
            list(
                ExperimentStudent.objects.filter(experiment=self.experiment)
                .order_by("student_id")
                .values_list("pk", "student_id", "class_snapshot_name")
            ),
            roster_before,
        )
        self.assertEqual(
            list(
                self.experiment.participant_class_links.order_by("class_group_id").values_list(
                    "class_group_id", flat=True
                )
            ),
            class_rules_before,
        )
        self.assertFalse(self._publish_audits(self.experiment).exists())

    def test_task_conflict_creates_no_publish_audit(self):
        self._configure_course_roster(self.class_a)
        existing_version = ExperimentVersion.objects.create(
            experiment=self.experiment,
            version_number=1,
            published_by=self.teacher,
        )
        existing_snapshot = ExperimentClassSnapshot.objects.create(
            version=existing_version,
            source_class_group=self.class_a,
            name_snapshot=self.class_a.name,
            academic_year_snapshot=self.class_a.academic_year,
            capture_mode=ExperimentClassSnapshot.CaptureMode.NATIVE,
        )
        with transaction.atomic():
            with _class_experiment_task_publication_scope():
                ClassExperimentTask.objects.create(
                    class_group=self.class_a,
                    experiment=self.experiment,
                    release_snapshot=existing_version,
                    class_snapshot=existing_snapshot,
                    published_at=existing_version.published_at,
                )

        with self.assertRaises(IntegrityError):
            publish_experiment(self.experiment, self.teacher)

        self.assertFalse(self._publish_audits(self.experiment).exists())

    def test_validation_failure_creates_no_publish_audit(self):
        with self.assertRaises(ValidationError):
            publish_experiment(self.experiment, self.teacher)

        self.assertFalse(self._publish_audits(self.experiment).exists())

    def _create_legacy_experiment(self):
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            name="SENSITIVE-LEGACY-BANK-NAME",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="SENSITIVE-LEGACY-QUESTION-TEXT",
            correct_answer="SENSITIVE-LEGACY-ANSWER",
        )
        concept = Concept.objects.create(
            owner=self.teacher,
            scope=Concept.Scope.PRIVATE,
            name="SENSITIVE-LEGACY-KC",
            normalized_name="sensitive-legacy-kc",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        experiment = Experiment.objects.create(
            owner=self.teacher,
            question_bank=bank,
            name="SENSITIVE-LEGACY-EXPERIMENT-NAME",
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=question,
            display_order=1,
        )
        ExperimentStudent.objects.create(
            experiment=experiment,
            student=self.student_a,
            class_snapshot_name="SENSITIVE-LEGACY-CLASS-LABEL",
        )
        return experiment
