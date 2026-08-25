from threading import Event, Lock, Thread
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.accounts.enrollment_services import remove_course_enrollment
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.services import assign_students, publish_experiment
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import (
    allocate_course_question_identity,
)

from .models import DraftSaveEvent, ExperimentSession
from .services import (
    finish_session,
    get_or_create_session,
    mark_skipped,
    record_visit,
    save_draft,
    submit_attempt,
)


@skipUnlessDBFeature("has_select_for_update_of")
class PostgreSQLEnrollmentRemovalRaceTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.teacher = User.objects.create_user(
            username="postgres-race-teacher",
            role=User.Role.TEACHER,
        )
        self.student = User.objects.create_user(
            username="POSTGRES-RACE-STUDENT",
            student_number="POSTGRES-RACE-STUDENT",
            role=User.Role.STUDENT,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="选课移出并发课程",
            code="RACE101",
            term="2026秋",
        )
        self.class_group = ClassGroup.objects.create(
            course=self.course,
            name="并发实验班",
            academic_year="2025",
        )
        self.enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            owner=self.teacher,
            name="并发题库",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="移出后能否创建会话？",
            correct_answer="不能",
        )
        allocate_course_question_identity(self.course, question)
        concept = Concept.objects.create(course=self.course, name="并发访问控制")
        QuestionConcept.objects.create(
            question=question,
            concept=concept,
            is_primary=True,
        )
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            owner=self.teacher,
            question_bank=bank,
            name="并发发布实验",
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
        assign_students(self.experiment, [self.student])
        publish_experiment(self.experiment, self.teacher)

    def test_session_creation_linearizes_before_enrollment_removal(self):
        session_reached_write = Event()
        allow_session_write = Event()
        removal_started = Event()
        removal_done = Event()
        order_lock = Lock()
        commit_order = []
        errors = []
        original_session_write = ExperimentSession.objects.get_or_create

        def delayed_session_write(*args, **kwargs):
            session_reached_write.set()
            if not allow_session_write.wait(timeout=5):
                raise TimeoutError("session write was not released")
            result = original_session_write(*args, **kwargs)
            with order_lock:
                commit_order.append("session")
            return result

        def create_session():
            close_old_connections()
            try:
                with patch.object(
                    ExperimentSession.objects,
                    "get_or_create",
                    side_effect=delayed_session_write,
                ):
                    get_or_create_session(
                        Experiment.objects.get(pk=self.experiment.pk),
                        User.objects.get(pk=self.student.pk),
                    )
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def remove_enrollment():
            close_old_connections()
            removal_started.set()
            try:
                remove_course_enrollment(
                    course=Course.objects.get(pk=self.course.pk),
                    class_group=ClassGroup.objects.get(pk=self.class_group.pk),
                    enrollment=CourseEnrollment.objects.get(pk=self.enrollment.pk),
                )
                with order_lock:
                    commit_order.append("removal")
            except Exception as exc:
                errors.append(exc)
            finally:
                removal_done.set()
                close_old_connections()

        session_thread = Thread(target=create_session)
        removal_thread = Thread(target=remove_enrollment)
        session_thread.start()
        self.assertTrue(session_reached_write.wait(timeout=5))
        removal_thread.start()
        self.assertTrue(removal_started.wait(timeout=5))

        removal_done.wait(timeout=0.5)
        allow_session_write.set()
        session_thread.join(timeout=5)
        removal_thread.join(timeout=5)

        self.assertFalse(session_thread.is_alive())
        self.assertFalse(removal_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(commit_order, ["session", "removal"])
        self.assertFalse(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertTrue(
            ExperimentSession.objects.filter(student=self.student).exists()
        )

    def test_existing_session_write_linearizes_before_enrollment_removal(self):
        session = get_or_create_session(self.experiment, self.student)
        attempt = session.attempts.get()
        event_reached_write = Event()
        allow_event_write = Event()
        removal_started = Event()
        removal_done = Event()
        order_lock = Lock()
        commit_order = []
        errors = []
        original_event_write = DraftSaveEvent.objects.create

        def delayed_event_write(*args, **kwargs):
            event_reached_write.set()
            if not allow_event_write.wait(timeout=5):
                raise TimeoutError("research event write was not released")
            result = original_event_write(*args, **kwargs)
            with order_lock:
                commit_order.append("event")
            return result

        def write_existing_session():
            close_old_connections()
            try:
                with patch.object(
                    DraftSaveEvent.objects,
                    "create",
                    side_effect=delayed_event_write,
                ):
                    save_draft(attempt.pk, {"answer": "并发草稿"})
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def remove_enrollment():
            close_old_connections()
            removal_started.set()
            try:
                remove_course_enrollment(
                    course=Course.objects.get(pk=self.course.pk),
                    class_group=ClassGroup.objects.get(pk=self.class_group.pk),
                    enrollment=CourseEnrollment.objects.get(pk=self.enrollment.pk),
                )
                with order_lock:
                    commit_order.append("removal")
            except Exception as exc:
                errors.append(exc)
            finally:
                removal_done.set()
                close_old_connections()

        writer_thread = Thread(target=write_existing_session)
        removal_thread = Thread(target=remove_enrollment)
        writer_thread.start()
        self.assertTrue(event_reached_write.wait(timeout=5))
        removal_thread.start()
        self.assertTrue(removal_started.wait(timeout=5))

        removal_done.wait(timeout=0.5)
        allow_event_write.set()
        writer_thread.join(timeout=5)
        removal_thread.join(timeout=5)

        self.assertFalse(writer_thread.is_alive())
        self.assertFalse(removal_thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(commit_order, ["event", "removal"])
        self.assertFalse(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertEqual(DraftSaveEvent.objects.filter(attempt=attempt).count(), 1)

    def test_course_session_write_lifecycle_avoids_nullable_outer_join_locks(self):
        session = get_or_create_session(self.experiment, self.student)
        assignment = session.assignments.get()

        attempt = record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, {"answer": "草稿"})
        mark_skipped(attempt.pk)
        record_visit(session.pk, assignment.pk)
        save_draft(attempt.pk, {"answer": "不能"})
        submitted = submit_attempt(attempt.pk, uuid4())
        completed = finish_session(session.pk)

        self.assertEqual(submitted.status, submitted.Status.SUBMITTED)
        self.assertEqual(completed.status, ExperimentSession.Status.COMPLETED)
