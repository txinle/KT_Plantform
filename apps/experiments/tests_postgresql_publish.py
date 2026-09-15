from queue import Queue
from threading import Event, Thread, current_thread
from unittest.mock import patch

from django.db import close_old_connections
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentQuestion,
    ExperimentSamplingKC,
    ExperimentStudent,
    ExperimentVersion,
    ExperimentVersionParticipantSnapshot,
    PublishPlan,
    PublishPlanClass,
    QuestionSnapshot,
)
from .publication_services import (
    PublicationConflict,
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_selection,
)
from .services import publish_experiment, save_participant_rules


@skipUnlessDBFeature("has_select_for_update_of")
class PostgreSQLPublishLockRegressionTests(TestCase):
    """Exercise the nullable course-identity joins under real row locking."""

    def test_publish_locks_only_base_rows_when_course_identity_is_nullable(self):
        teacher = User.objects.create_user(
            username="postgres-publish-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
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
        ExperimentStudent.objects.create(experiment=experiment, student=self.student)

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
        self.student = User.objects.create_user(
            username="PG-CONCURRENT-STUDENT",
            student_number="PG-CONCURRENT-STUDENT",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
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
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        save_participant_rules(self.experiment, [self.class_group], [], [])

    def test_two_publish_transactions_commit_one_complete_release(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        plan = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        expected_revision = plan.plan_revision
        start = Event()
        outcomes = Queue()

        def commit_as_contender():
            close_old_connections()
            try:
                if not start.wait(timeout=10):
                    raise AssertionError("concurrent commit was not released")
                worker_plan = PublishPlan.objects.get(pk=plan.pk)
                actor = User.objects.get(pk=self.teacher.pk)
                tasks = commit_publish_plan(
                    worker_plan,
                    actor,
                    expected_revision=expected_revision,
                )
                outcomes.put(("committed", tasks[0].pk))
            except Exception as exc:
                outcomes.put(("failed", exc))
            finally:
                close_old_connections()

        first = Thread(target=commit_as_contender, name="publish-winner")
        contender = Thread(target=commit_as_contender, name="publish-contender")
        first.start()
        contender.start()
        start.set()
        first.join(timeout=20)
        contender.join(timeout=20)

        self.assertFalse(first.is_alive(), "first publish transaction did not finish")
        self.assertFalse(contender.is_alive(), "contending publish transaction did not finish")
        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        committed = [value for status, value in results if status == "committed"]
        failures = [value for status, value in results if status == "failed"]

        self.assertEqual(len(committed), 1)
        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], PublicationConflict)

        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.FROZEN)
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
            PublishPlan.objects.filter(
                experiment=self.experiment,
                status=PublishPlan.Status.COMMITTED,
            ).count(),
            1,
        )

    def test_resume_plan_and_preview_share_course_experiment_plan_lock_order(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        PublishPlanClass.objects.create(
            publish_plan=plan,
            class_group=self.class_group,
        )
        preview_reached_plan_lock = Event()
        resume_started = Event()
        resume_reached_plan_lock = Event()
        outcomes = Queue()

        from . import publication_services

        original_lock_plan = publication_services._lock_editable_publish_plan
        original_select_plan_for_update = PublishPlan.objects.select_for_update

        def pause_after_plan_lock(*args, **kwargs):
            result = original_lock_plan(*args, **kwargs)
            if current_thread().name == "preview-lock-order":
                preview_reached_plan_lock.set()
                if not resume_started.wait(timeout=10):
                    raise AssertionError("resume transaction never started")
                resume_reached_plan_lock.wait(timeout=1)
            return result

        def observe_plan_lock(*args, **kwargs):
            if current_thread().name == "resume-lock-order":
                resume_reached_plan_lock.set()
            return original_select_plan_for_update(*args, **kwargs)

        def preview_worker():
            close_old_connections()
            try:
                worker_plan = type(plan).objects.get(pk=plan.pk)
                actor = User.objects.get(pk=self.teacher.pk)
                preview = generate_preview(
                    worker_plan,
                    actor,
                    expected_revision=worker_plan.plan_revision,
                )
                outcomes.put(("preview", preview.pk))
            except Exception as exc:  # pragma: no cover - asserted below
                outcomes.put(("failed", exc))
            finally:
                close_old_connections()

        def resume_worker():
            close_old_connections()
            try:
                if not preview_reached_plan_lock.wait(timeout=10):
                    raise AssertionError("preview never reached the plan lock")
                resume_started.set()
                experiment = Experiment.objects.get(pk=self.experiment.pk)
                actor = User.objects.get(pk=self.teacher.pk)
                resumed = get_or_create_publish_plan(experiment, actor)
                outcomes.put(("resume", resumed.pk))
            except Exception as exc:  # pragma: no cover - asserted below
                outcomes.put(("failed", exc))
            finally:
                close_old_connections()

        with (
            patch.object(
                publication_services,
                "_lock_editable_publish_plan",
                side_effect=pause_after_plan_lock,
            ),
            patch.object(
                PublishPlan.objects,
                "select_for_update",
                side_effect=observe_plan_lock,
            ),
        ):
            preview_thread = Thread(target=preview_worker, name="preview-lock-order")
            resume_thread = Thread(target=resume_worker, name="resume-lock-order")
            preview_thread.start()
            resume_thread.start()
            preview_thread.join(timeout=20)
            resume_thread.join(timeout=20)

        self.assertFalse(preview_thread.is_alive(), "preview transaction deadlocked")
        self.assertFalse(resume_thread.is_alive(), "resume transaction deadlocked")
        results = [outcomes.get_nowait(), outcomes.get_nowait()]
        self.assertEqual(
            {status for status, _ in results}, {"preview", "resume"}, results
        )
        self.assertEqual(
            {value for status, value in results if status == "resume"},
            {plan.pk},
        )

    def test_student_start_does_not_lock_the_shared_course_row(self):
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        plan = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan, self.teacher, expected_revision=plan.plan_revision
        )[0]

        from apps.assessment import services as assessment_services

        with patch.object(
            Course.objects,
            "select_for_update",
            side_effect=AssertionError("student start must not lock the course row"),
        ):
            session = assessment_services.get_or_create_session(
                Experiment.objects.get(pk=self.experiment.pk),
                User.objects.get(pk=self.student.pk),
                class_task=ClassExperimentTask.objects.get(pk=task.pk),
            )

        self.assertEqual(session.class_task_id, task.pk)
