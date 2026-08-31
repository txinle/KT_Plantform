import json
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentSamplingKC, ExperimentStudent
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_selection,
)
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from .models import DraftSaveEvent, ExperimentSession


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

    def test_student_selects_course_before_experiment(self):
        self.client.force_login(self.student)

        dashboard = self.client.get(reverse("assessment:dashboard"))
        self.assertContains(dashboard, "我的课程")
        self.assertContains(dashboard, "高等数学")
        self.assertNotContains(dashboard, "开始实验")

        course_page = self.client.get(reverse("assessment:course", args=[self.course.pk]))
        self.assertContains(course_page, "MATH101")
        self.assertContains(course_page, "测验")
        self.assertContains(course_page, "开始实验")

    def test_student_navigation_keeps_assigned_numbers_and_shows_the_stable_course_code(self):
        self.client.force_login(self.student)
        self.client.post(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        assignment = session.assignments.get()

        response = self.client.get(
            reverse("assessment:question", args=[session.pk, assignment.pk])
        )

        self.assertContains(response, "第 1 题")
        self.assertContains(response, self.question_identity.stable_code)

    def test_student_cannot_open_course_without_active_enrollment(self):
        self.client.force_login(self.other_student)

        response = self.client.get(reverse("assessment:course", args=[self.course.pk]))

        self.assertEqual(response.status_code, 404)

    def test_archived_course_cannot_create_new_session(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.client.force_login(self.student)

        response = self.client.post(reverse("assessment:start", args=[self.experiment.pk]))

        self.assertRedirects(response, reverse("assessment:dashboard"))
        self.assertFalse(ExperimentSession.objects.exists())

    def test_experiment_assignment_without_course_enrollment_cannot_start(self):
        ExperimentStudent.objects.create(
            experiment=self.experiment,
            student=self.other_student,
            class_snapshot_name="错误分配",
        )
        self.client.force_login(self.other_student)

        response = self.client.post(reverse("assessment:start", args=[self.experiment.pk]))

        self.assertRedirects(response, reverse("assessment:dashboard"))
        self.assertFalse(ExperimentSession.objects.filter(student=self.other_student).exists())

    def test_start_locks_course_enrollment_before_creating_a_session(self):
        lock_order = []
        original_course_lock = Course.objects.select_for_update
        original_enrollment_lock = CourseEnrollment.objects.select_for_update
        original_session_write = ExperimentSession.objects.create

        def lock_course(*args, **kwargs):
            lock_order.append("course")
            return original_course_lock(*args, **kwargs)

        def lock_enrollment(*args, **kwargs):
            lock_order.append("enrollment")
            return original_enrollment_lock(*args, **kwargs)

        def write_session(*args, **kwargs):
            lock_order.append("session")
            return original_session_write(*args, **kwargs)

        self.client.force_login(self.student)
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
                ExperimentSession.objects,
                "create",
                side_effect=write_session,
            ),
        ):
            self.client.post(reverse("assessment:start", args=[self.experiment.pk]))

        self.assertEqual(lock_order[:3], ["course", "enrollment", "session"])

    def test_archiving_course_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.client.post(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])

        response = self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": "2"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        attempt.refresh_from_db()
        self.assertIsNone(attempt.draft_answer)
        self.assertFalse(DraftSaveEvent.objects.exists())

    def test_inactive_enrollment_blocks_writes_to_existing_session(self):
        self.client.force_login(self.student)
        self.client.post(reverse("assessment:start", args=[self.experiment.pk]))
        session = ExperimentSession.objects.get(student=self.student)
        attempt = session.attempts.get()
        CourseEnrollment.objects.filter(course=self.course, student=self.student).update(is_active=False)

        response = self.client.post(
            reverse("assessment:save_draft", args=[attempt.pk]),
            data=json.dumps({"answer": "2"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertFalse(DraftSaveEvent.objects.exists())

    def test_existing_session_write_locks_enrollment_before_research_event(self):
        self.client.force_login(self.student)
        self.client.post(reverse("assessment:start", args=[self.experiment.pk]))
        attempt = ExperimentSession.objects.get(student=self.student).attempts.get()
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
                reverse("assessment:save_draft", args=[attempt.pk]),
                data=json.dumps({"answer": "2"}),
                content_type="application/json",
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(lock_order[:3], ["course", "enrollment", "event"])
