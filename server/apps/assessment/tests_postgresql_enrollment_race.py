from threading import Barrier, Event, Lock, Thread
from unittest.mock import patch
from uuid import uuid4

from django.db import close_old_connections
from django.test import TransactionTestCase, skipUnlessDBFeature

from apps.accounts.enrollment_services import remove_course_enrollment
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentSamplingKC
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_selection,
)
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
        self.student_b = User.objects.create_user(
            username="POSTGRES-RACE-STUDENT-B",
            student_number="POSTGRES-RACE-STUDENT-B",
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
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student_b,
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
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment, concept=concept
        )
        ExperimentQuestion.objects.create(
            experiment=self.experiment,
            question=question,
            display_order=1,
        )
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
        # Historical write/removal races use the retained domain contract, not public activation.
        with patch('apps.assessment.prepared_services.prepare_participant', return_value=None):
            commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        self.experiment.refresh_from_db()

    def test_two_students_in_one_task_save_without_a_shared_row_lock(self):
        first = get_or_create_session(self.experiment, self.student)
        second = get_or_create_session(self.experiment, self.student_b)
        first_attempt = first.attempts.get()
        second_attempt = second.attempts.get()
        task_locked = Event()
        release_task = Event()
        second_finished = Event()
        errors = []

        def hold_shared_task_row():
            close_old_connections()
            try:
                from django.db import transaction
                from apps.experiments.models import ClassExperimentTask

                with transaction.atomic():
                    ClassExperimentTask.objects.select_for_update().get(
                        pk=first.class_task_id
                    )
                    task_locked.set()
                    if not release_task.wait(timeout=5):
                        raise TimeoutError("shared task lock was not released")
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        def save_second_student():
            close_old_connections()
            try:
                save_draft(second_attempt.pk, {"answer": "B"})
                second_finished.set()
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        first_thread = Thread(target=hold_shared_task_row)
        second_thread = Thread(target=save_second_student)
        first_thread.start()
        self.assertTrue(task_locked.wait(timeout=5))
        second_thread.start()
        try:
            self.assertTrue(
                second_finished.wait(timeout=3),
                "second student was blocked by the shared class-task row",
            )
        finally:
            release_task.set()
            first_thread.join(timeout=5)
            second_thread.join(timeout=5)

        self.assertEqual(errors, [])
        first_attempt.refresh_from_db()
        second_attempt.refresh_from_db()
        self.assertEqual(second_attempt.status, second_attempt.Status.DRAFT)

    def test_duplicate_start_requests_return_one_session(self):
        barrier = Barrier(2)
        session_ids = []
        errors = []
        def start():
            close_old_connections()
            try:
                barrier.wait(timeout=5)
                session = get_or_create_session(
                    Experiment.objects.get(pk=self.experiment.pk),
                    User.objects.get(pk=self.student.pk),
                )
                session_ids.append(session.pk)
            except Exception as exc:
                errors.append(exc)
            finally:
                close_old_connections()

        threads = [Thread(target=start) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertEqual(len(session_ids), 2)
        self.assertEqual(len(set(session_ids)), 1)
        self.assertEqual(
            ExperimentSession.objects.filter(student=self.student).count(), 1
        )

    def test_session_creation_linearizes_before_enrollment_removal(self):
        session_reached_write = Event()
        allow_session_write = Event()
        removal_started = Event()
        removal_done = Event()
        order_lock = Lock()
        commit_order = []
        errors = []
        original_session_write = ExperimentSession.objects.create

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
                    "create",
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
        self.assertCountEqual(commit_order, ["session", "removal"])
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.is_active)
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
        self.assertCountEqual(commit_order, ["event", "removal"])
        self.enrollment.refresh_from_db()
        self.assertFalse(self.enrollment.is_active)
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
