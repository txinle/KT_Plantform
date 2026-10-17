from apps.api.test_support import domain_payload
import json
import uuid
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import ClassExperimentTask, Experiment, ExperimentSamplingKC, ExperimentStudent
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_selection,
)
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from .models import DraftSaveEvent, ExperimentSession
from .test_support import accept_research_consent


@override_settings(ASSESSMENT_ADMISSION_ENABLED=True, ASSESSMENT_ADMISSION_GLOBAL_ENABLED=True,
                   ASSESSMENT_PREPARED_ADMISSION_MODE='postgres_transaction')
class StudentCourseNavigationTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="teacher", role=User.Role.TEACHER)
        self.student = User.objects.create_user(
            username="S1",
            student_number="S1",
            role=User.Role.STUDENT,
        )
        self.other_student = User.objects.create_user(
            username="S2",
            student_number="S2",
            role=User.Role.STUDENT,
        )
        accept_research_consent(self.student, self.other_student)
        self.course = Course.objects.create(
            owner=self.teacher,
            name="高等数学",
            code="MATH101",
            term="2026春",
        )
        self.class_group = ClassGroup.objects.create(course=self.course, name="一班")
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="题库",
        )
        concept = Concept.objects.create(course=self.course, name="加法")
        question = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="1+1",
            correct_answer="2",
        )
        self.question_identity = allocate_course_question_identity(self.course, question)
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        self.experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=bank,
            name="测验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
        )
        ExperimentSamplingKC.objects.create(
            experiment=self.experiment,
            concept=concept,
        )
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        plan = save_plan_selection(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
            class_ids=[self.class_group.pk],
            exclusions_by_class={},
        )
        generate_preview(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        plan.refresh_from_db()
        commit_publish_plan(
            plan,
            self.teacher,
            expected_revision=plan.plan_revision,
        )
        self.experiment.refresh_from_db()
        self.task = ClassExperimentTask.objects.get(experiment=self.experiment)


    def start(self):
        return self.client.post(reverse('student-task-start', args=[self.task.pk]),
            data={'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())},
            content_type='application/json')

    def draft_document(self, attempt):
        from .capture_v2_services import ingest_capture_batch
        from .test_capture_v2 import CaptureV2Tests
        self.session = attempt.session
        self.stream = attempt.writer_client_instance_id
        event = CaptureV2Tests.transition(self, attempt, '', '2', client=self.stream)
        ingest_capture_batch(student=self.student,
            document={'client_instance_id': str(self.stream), 'events': [event]})
        return {'target_event_id': event['event_id'], 'expected_version': attempt.response_version,
            'writer_lease_token': str(attempt.writer_lease_token),
            'writer_client_instance_id': str(self.stream)}

    def test_student_navigation_keeps_assigned_numbers_and_shows_the_stable_course_code(self):
        self.client.force_login(self.student)
        self.start()
        session = ExperimentSession.objects.get(student=self.student)
        assignment = session.assignments.get()

        response = self.client.get(
            f"/api/v1/student/sessions/{session.pk}/questions/{assignment.pk}/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)["assignment"]["sequence_index"], 1)
        self.assertEqual(
            domain_payload(response)["question"]["stable_code"],
            self.question_identity.stable_code,
        )

    def test_student_cannot_open_course_without_active_enrollment(self):
        self.client.force_login(self.other_student)

        response = self.client.get(reverse("student-tasks", args=[self.course.pk]))

        self.assertEqual(response.status_code, 404)

    def test_archived_course_cannot_create_new_session(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.student)

        response = self.start()

        self.assertEqual(response.status_code, 409, response.content)
        self.assertFalse(ExperimentSession.objects.exclude(started_at=None).exists())

    def test_experiment_assignment_without_course_enrollment_cannot_start(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.other_student,
            class_snapshot_name="错误分配",
        )
        self.client.force_login(self.other_student)

        response = self.start()

        self.assertEqual(response.status_code, 404, response.content)
        self.assertFalse(ExperimentSession.objects.filter(student=self.other_student).exists())

    def test_start_activates_a_prepared_session_without_shared_course_or_enrollment_locks(self):
        self.client.force_login(self.student)
        session = ExperimentSession.objects.get(student=self.student)
        self.assertEqual(session.status, ExperimentSession.Status.PREPARED)
        with CaptureQueriesContext(connection) as queries:
            response = self.start()
        self.assertEqual(response.status_code, 201, response.content)
        locks = [row['sql'] for row in queries if 'FOR UPDATE' in row['sql'] or 'FOR NO KEY UPDATE' in row['sql']]
        self.assertTrue(any('FROM "assessment_experimentsession"' in sql for sql in locks), locks)
        self.assertFalse(any('FROM "accounts_course"' in sql or 'FROM "accounts_courseenrollment"' in sql for sql in locks), locks)
        self.assertFalse(any(row['sql'].startswith('INSERT INTO "assessment_experimentsession"') for row in queries))
        session.refresh_from_db()
        self.assertEqual(session.status, ExperimentSession.Status.IN_PROGRESS)

    def test_archiving_course_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.start()
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        document = self.draft_document(attempt)
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        response = self.client.post(
            reverse("assessment_api:save_draft", args=[attempt.pk]),
            data=json.dumps(document),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)
        self.assertFalse(DraftSaveEvent.objects.exists())

    def test_inactive_enrollment_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.start()
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        document = self.draft_document(attempt)
        CourseEnrollment.objects.filter(course=self.course, student=self.student).update(is_active=False)

        response = self.client.post(
            reverse("assessment_api:save_draft", args=[attempt.pk]),
            data=json.dumps(document),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(DraftSaveEvent.objects.exists())

    def test_existing_session_write_avoids_shared_course_and_enrollment_locks(self):
        self.client.force_login(self.student)
        self.start()
        attempt = ExperimentSession.objects.get(student=self.student).attempts.get()
        document = self.draft_document(attempt)
        lock_order = []
        original_course_lock = Course.objects.select_for_update
        original_enrollment_lock = CourseEnrollment.objects.select_for_update
        original_event_write = DraftSaveEvent.objects.create

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_enrollment(*args, **kwargs):
            lock_order.append("enrollment")
            return original_enrollment_lock(*args, **kwargs)

        def write_event(*args, **kwargs):
            lock_order.append("event")
            return original_event_write(*args, **kwargs)

        with (
            patch.object(
                Course.objects,
                "select_for_update",
                side_effect=lock_course,
            ),
            patch.object(
                CourseEnrollment.objects,
                "select_for_update",
                side_effect=lock_enrollment,
            ),
            patch.object(
                DraftSaveEvent.objects,
                "create",
                side_effect=write_event,
            ),
        ):
            response = self.client.post(
                reverse("assessment_api:save_draft", args=[attempt.pk]),
                data=json.dumps(document),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_order, ["event"])
