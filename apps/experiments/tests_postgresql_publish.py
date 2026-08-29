from queue import Queue
from threading import Event, Thread, current_thread
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.db import close_old_connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.audit.models import AuditLog
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

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
from .services import publish_experiment, save_participant_rules
from . import services


@skipUnlessDBFeature("has_select_for_update_of")
class PostgreSQLPublishLockRegressionTests(TestCase):
    """Exercise the nullable course-identity joins under real row locking."""

    def test_publish_locks_only_base_rows_when_course_identity_is_nullable(self):
        teacher = User.objects.create_user(
            username="postgres-publish-teacher",
            role=User.Role.TEACHER,
        )
        student = User.objects.create_user(
            username="POSTGRES-PUBLISH-STUDENT",
            student_number="POSTGRES-PUBLISH-STUDENT",
            role=User.Role.STUDENT,
        )
        bank = QuestionBank.objects.create(owner=teacher, name="PostgreSQL 发布题库")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="PostgreSQL 行锁是否兼容？",
            correct_answer="是",
        )
        concept = Concept.objects.create(
            owner=teacher,
            scope=Concept.Scope.PRIVATE,
            name="发布行锁",
            normalized_name="发布行锁",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        experiment = Experiment.objects.create(
            owner=teacher,
            question_bank=bank,
            name="PostgreSQL 发布回归",
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=question,
            display_order=1,
        )
        ExperimentStudent.objects.create(experiment=experiment, student=student)

        version = publish_experiment(experiment, teacher)

        self.assertEqual(version.version_number, 1)
        self.assertTrue(ExperimentVersion.objects.filter(pk=version.pk).exists())
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, Experiment.Status.PUBLISHED)


@skipUnlessDBFeature("has_select_for_update_of")
class PostgreSQLConcurrentPublishRegressionTests(TransactionTestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="postgres-concurrent-publish-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="PostgreSQL 并发发布课程",
            code="PG-CONCURRENT-PUBLISH",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="PostgreSQL 并发发布班",
            academic_year="2026",
        )
        student = User.objects.create_user(
            username="PG-CONCURRENT-STUDENT",
            student_number="PG-CONCURRENT-STUDENT",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="PostgreSQL 并发发布题库",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="并发发布是否只产生一个正式任务？",
            correct_answer="是",
        )
        concept = Concept.objects.create(
            course=self.course,
            name="并发发布",
        )
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="PostgreSQL 并发发布实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        save_participant_rules(self.experiment, [self.class_group], [], [])

    def test_two_publish_transactions_commit_one_complete_release(self):
        first_lock_acquired = Event()
        contender_entered_lock = Event()
        outcomes = Queue()
        original_lock_draft_experiment = services._lock_draft_experiment

        def observe_contender_lock(experiment, *, error_message):
            if current_thread().name == "publish-contender":
                contender_entered_lock.set()
            return original_lock_draft_experiment(
                experiment,
                error_message=error_message,
            )

        def publish_while_holding_first_lock():
            close_old_connections()
            try:
                with transaction.atomic():
                    experiment = Experiment.objects.select_for_update().get(
                        pk=self.experiment.pk
                    )
                    first_lock_acquired.set()
                    if not contender_entered_lock.wait(timeout=10):
                        raise AssertionError("contending publish never reached the row lock")
                    actor = User.objects.get(pk=self.teacher.pk)
                    version = publish_experiment(experiment, actor)
                outcomes.put(("committed", version.pk))
            except Exception as exc:  # pragma: no cover - asserted through outcomes
                outcomes.put(("failed", exc))
            finally:
                close_old_connections()

        def publish_as_contender():
            close_old_connections()
            try:
                if not first_lock_acquired.wait(timeout=10):
                    raise AssertionError("first publish never acquired the row lock")
                experiment = Experiment.objects.get(pk=self.experiment.pk)
                actor = User.objects.get(pk=self.teacher.pk)
                version = publish_experiment(experiment, actor)
                outcomes.put(("committed", version.pk))
            except Exception as exc:
                outcomes.put(("failed", exc))
            finally:
                close_old_connections()

        with patch.object(
            services,
            "_lock_draft_experiment",
            side_effect=observe_contender_lock,
        ):
            first = Thread(
                target=publish_while_holding_first_lock,
                name="publish-winner",
            )
            contender = Thread(
                target=publish_as_contender,
                name="publish-contender",
            )
            first.start()
            contender.start()
            first.join(timeout=20)
            contender.join(timeout=20)

        self.assertFalse(first.is_alive(), "first publish transaction did not finish")
        self.assertFalse(contender.is_alive(), "contending publish transaction did not finish")
        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        committed = [value for status, value in results if status == "committed"]
        failures = [value for status, value in results if status == "failed"]

        self.assertEqual(len(committed), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], ValidationError)
        self.assertIn("只有草稿实验可以发布", failures[0].messages)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.PUBLISHED)
        self.assertEqual(
            ExperimentVersion.objects.filter(experiment=self.experiment).count(), 1
        )
        self.assertEqual(
            ClassExperimentTask.objects.filter(experiment=self.experiment).count(), 1
        )
        self.assertEqual(
            ExperimentClassSnapshot.objects.filter(version__experiment=self.experiment).count(),
            1,
        )
        self.assertEqual(
            ExperimentVersionParticipantSnapshot.objects.filter(
                version__experiment=self.experiment
            ).count(),
            1,
        )
        self.assertEqual(
            QuestionSnapshot.objects.filter(version__experiment=self.experiment).count(),
            1,
        )
        self.assertEqual(
            AuditLog.objects.filter(
                action="experiment.publish",
                object_id=str(self.experiment.pk),
            ).count(),
            1,
        )
