from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from django.core.exceptions import PermissionDenied
from django.test import TestCase, override_settings
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User
from apps.api.test_support import domain_payload
from apps.assessment.models import AssessmentEvent, ExperimentSession
from apps.assessment.prepared_services import activate_prepared_session
from apps.assessment.services import get_or_create_session, resume_task_session
from apps.assessment.test_support import accept_research_consent
from apps.audit.models import AuditLog
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept

from .models import ClassExperimentTask, Experiment, ExperimentSamplingKC
from .publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_schedules,
    save_plan_selection,
)
from .services import archive_experiment


class TeacherExperimentArchiveApiTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="archive-owner", role=User.Role.TEACHER)
        self.course = Course.objects.create(owner=self.teacher, code="ARCHIVE", name="归档课程", term="2026")
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name="归档题库")
        self.experiment = Experiment.objects.create(
            course=self.course, owner=self.teacher, question_bank=self.bank, name="归档实验",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=1,
        )
        self.url = f"/api/v1/teacher/courses/{self.course.pk}/experiments/{self.experiment.pk}/archive/"
        self.client.force_login(self.teacher)

    def test_owner_archives_once_and_repeat_preserves_timestamp_and_audit(self):
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.content)
        payload = domain_payload(response)
        self.assertEqual(payload["status"], Experiment.Status.ARCHIVED)
        self.assertFalse(payload["can_archive"])
        self.experiment.refresh_from_db()
        archived_at = self.experiment.updated_at
        repeated = self.client.post(self.url)
        self.assertEqual(repeated.status_code, 200, repeated.content)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.updated_at, archived_at)
        audit = AuditLog.objects.get(action="experiment.archive", object_id=str(self.experiment.pk))
        self.assertEqual(audit.actor_id, self.teacher.pk)
        self.assertEqual(audit.details["previous_status"], Experiment.Status.DRAFT)

    def test_collaborating_teacher_can_archive(self):
        collaborator = User.objects.create_user(username="archive-collaborator", role=User.Role.TEACHER)
        CourseTeacher.objects.create(course=self.course, teacher=collaborator)
        self.client.force_login(collaborator)
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.content)

    def test_student_role_is_denied(self):
        student = User.objects.create_user(username="archive-student", role=User.Role.STUDENT)
        self.client.force_login(student)
        self.assertEqual(self.client.post(self.url).status_code, 403)

    def test_inaccessible_course_and_cross_course_experiment_are_not_disclosed(self):
        outsider = User.objects.create_user(username="archive-outsider", role=User.Role.TEACHER)
        self.client.force_login(outsider)
        self.assertEqual(self.client.post(self.url).status_code, 404)
        self.client.force_login(self.teacher)
        other_course = Course.objects.create(owner=self.teacher, code="OTHER", name="其他课程", term="2026")
        other_url = self.url.replace(f"courses/{self.course.pk}/", f"courses/{other_course.pk}/")
        self.assertEqual(self.client.post(other_url).status_code, 404)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)

    def test_archived_course_is_read_only(self):
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        response = self.client.post(self.url)
        self.assertEqual(response.status_code, 409, response.content)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)
        self.assertFalse(AuditLog.objects.filter(action="experiment.archive").exists())

    def test_service_rechecks_manager_permission(self):
        outsider = User.objects.create_user(username="archive-service-outsider", role=User.Role.TEACHER)
        with self.assertRaises(PermissionDenied):
            archive_experiment(course=self.course, experiment_id=self.experiment.pk, actor=outsider)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.DRAFT)

    def test_archive_capability_comes_from_course_permission_and_experiment_status(self):
        detail_url = self.url.removesuffix("archive/")
        self.assertTrue(domain_payload(self.client.get(detail_url))["can_archive"])
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        self.assertFalse(domain_payload(self.client.get(detail_url))["can_archive"])

    def publish_fixture(self):
        class_group = ClassGroup.objects.create(course=self.course, name="归档班级", academic_year="2026")
        student = User.objects.create_user(username="archive-participant", role=User.Role.STUDENT)
        CourseEnrollment.objects.create(course=self.course, class_group=class_group, student=student)
        question = Question.objects.create(
            bank=self.bank, sequence=1, code="ARCH-Q1", question_type=Question.Type.SHORT,
            text="归档测试题", correct_answer="答案",
        )
        concept = Concept.objects.create(course=self.course, code="KC1", name="测试知识点")
        QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
        ExperimentSamplingKC.objects.create(experiment=self.experiment, concept=concept)
        plan = get_or_create_publish_plan(self.experiment, self.teacher)
        save_plan_selection(plan, self.teacher, expected_revision=plan.plan_revision,
                            class_ids=[class_group.pk], exclusions_by_class={})
        plan.refresh_from_db()
        save_plan_schedules(plan, self.teacher, expected_revision=plan.plan_revision,
                            default_opens_at=timezone.now() - timedelta(minutes=1),
                            default_closes_at=timezone.now() + timedelta(hours=1), overrides={})
        plan.refresh_from_db()
        generate_preview(plan, self.teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, self.teacher, expected_revision=plan.plan_revision)
        task = ClassExperimentTask.objects.get(experiment=self.experiment)
        accept_research_consent(student)
        return task, student

    def test_restore_returns_draft_to_its_previous_status_once(self):
        self.assertEqual(self.client.post(self.url).status_code, 200)
        restore_url = self.url.replace('/archive/', '/restore/')
        response = self.client.post(restore_url)
        self.assertEqual(response.status_code, 200, response.content)
        payload = domain_payload(response)
        self.assertEqual(payload['status'], Experiment.Status.DRAFT)
        self.assertFalse(payload['can_restore'])
        self.assertTrue(payload['can_archive'])
        self.experiment.refresh_from_db()
        restored_at = self.experiment.updated_at
        self.assertEqual(self.client.post(restore_url).status_code, 200)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.updated_at, restored_at)
        self.assertEqual(AuditLog.objects.filter(action='experiment.restore', object_id=str(self.experiment.pk)).count(), 1)

    @patch('apps.assessment.prepared_services.prepare_participant', new=lambda *_args, **_kwargs: None)
    def test_restore_preserves_frozen_release_and_class_task_facts(self):
        task, _student = self.publish_fixture()
        self.experiment.refresh_from_db()
        original_status = self.experiment.status
        version_id = self.experiment.frozen_release_snapshot_id
        task_before = ClassExperimentTask.objects.filter(pk=task.pk).values().get()
        self.assertEqual(self.client.post(self.url).status_code, 200)
        self.assertTrue(domain_payload(self.client.get(self.url.removesuffix('archive/')))['can_restore'])
        response = self.client.post(self.url.replace('/archive/', '/restore/'))
        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, original_status)
        self.assertEqual(self.experiment.frozen_release_snapshot_id, version_id)
        self.assertEqual(ClassExperimentTask.objects.filter(pk=task.pk).values().get(), task_before)

    def test_restore_respects_course_permissions_and_read_only_course(self):
        self.assertEqual(self.client.post(self.url).status_code, 200)
        restore_url = self.url.replace('/archive/', '/restore/')
        stranger = User.objects.create_user(username='restore-stranger', role=User.Role.TEACHER)
        self.client.force_login(stranger)
        self.assertEqual(self.client.post(restore_url).status_code, 404)
        self.client.force_login(self.teacher)
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=['status'])
        self.assertEqual(self.client.post(restore_url).status_code, 409)
        self.experiment.refresh_from_db()
        self.assertEqual(self.experiment.status, Experiment.Status.ARCHIVED)

    @patch("apps.assessment.prepared_services.prepare_participant", new=lambda *_args, **_kwargs: None)
    def test_frozen_archive_preserves_evidence_schedule_and_active_session_resume(self):
        task, student = self.publish_fixture()
        session = get_or_create_session(self.experiment, student, class_task=task)
        attempt = session.attempts.get()
        attempt.draft_answer = "已有作答"
        attempt.save(update_fields=["draft_answer"])
        events_before = list(AssessmentEvent.objects.filter(session=session).values())
        self.experiment.refresh_from_db()
        version = self.experiment.frozen_release_snapshot
        frozen_configuration = version.configuration
        task_before = ClassExperimentTask.objects.filter(pk=task.pk).values().get()
        session_before = type(session).objects.filter(pk=session.pk).values().get()
        assignments_before = list(session.assignments.values())
        attempts_before = list(session.attempts.values())
        participants_before = list(version.participant_snapshots.values())
        snapshots_before = list(version.question_snapshots.values())

        response = self.client.post(self.url)

        self.assertEqual(response.status_code, 200, response.content)
        self.experiment.refresh_from_db()
        version.refresh_from_db()
        self.assertEqual(self.experiment.frozen_release_snapshot_id, version.pk)
        self.assertEqual(version.configuration, frozen_configuration)
        self.assertEqual(domain_payload(response)["sampling_total_count"], frozen_configuration["sampling_total_count"])
        self.assertEqual(ClassExperimentTask.objects.filter(pk=task.pk).values().get(), task_before)
        self.assertEqual(type(session).objects.filter(pk=session.pk).values().get(), session_before)
        self.assertEqual(list(session.assignments.values()), assignments_before)
        self.assertEqual(list(session.attempts.values()), attempts_before)
        self.assertEqual(list(version.participant_snapshots.values()), participants_before)
        self.assertEqual(list(version.question_snapshots.values()), snapshots_before)
        self.assertEqual(list(AssessmentEvent.objects.filter(session=session).values()), events_before)
        self.assertEqual(resume_task_session(task, student).pk, session.pk)
        publish_url = self.url.removesuffix("archive/") + "publish-plans/"
        self.assertEqual(self.client.post(publish_url).status_code, 409)

    def test_prepared_student_can_first_activate_after_archive_without_rebuilding_paper(self):
        with TemporaryDirectory() as directory, override_settings(ASSESSMENT_PAPER_ROOT=Path(directory)):
            task, student = self.publish_fixture()
            prepared = ExperimentSession.objects.get(class_task=task, student=student)
            self.assertEqual(prepared.status, ExperimentSession.Status.PREPARED)
            artifact_fields = ("id", "session_id", "preparation_version", "ready", "item_count",
                               "first_attempt_id", "manifest_descriptor", "first_runtime", "prepared_at")
            artifact_before = type(prepared.prepared_artifact).objects.filter(session=prepared).values(*artifact_fields).get()
            assignments_before = list(prepared.assignments.values_list("pk", "snapshot_id", "item_instance_id"))
            self.assertEqual(self.client.post(self.url).status_code, 200)

            activated, _ = activate_prepared_session(
                student=student, task_id=task.pk, client_instance_id=uuid4(), navigation_id=uuid4(),
            )

            self.assertEqual(activated.pk, prepared.pk)
            self.assertEqual(activated.status, ExperimentSession.Status.IN_PROGRESS)
            self.assertEqual(type(prepared.prepared_artifact).objects.filter(session=prepared).values(*artifact_fields).get(), artifact_before)
            self.assertEqual(list(activated.assignments.values_list("pk", "snapshot_id", "item_instance_id")), assignments_before)
