from apps.api.test_support import domain_payload
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import CourseEnrollment, User
from apps.analytics.models import (
    ExportJob,
    ExportSnapshot,
    ResearchSessionBehavior,
    ResearchSessionMaterialization,
    ResearchSessionResponse,
    ResearchSessionVisit,
)
from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.audit.models import AuditLog
from apps.question_bank.models import Question
from .models import Experiment, PublishPlan


class TestExperimentPurgeTests(StudentApiFixtureMixin, TestCase):
    def publish_task(self, *, name, **kwargs):
        return super().publish_task(name='test' if name == '正式学生任务' else name, **kwargs)

    def setUp(self):
        super().setUp()
        self.course.code = 'KT01'
        self.course.name = 'EDA技术'
        self.course.save(update_fields=['code', 'name'])
        started = self.start()
        self.session = ExperimentSession.objects.get(pk=domain_payload(started)['session_id'])
        self.submit_attempt_without_finishing(self.session)
        self.other_experiment, self.other_task = self.publish_task(name='保留正式实验', feedback_mode=self.feedback_mode)
        other_started = self.start(task=self.other_task)
        self.other_session = ExperimentSession.objects.get(pk=domain_payload(other_started)['session_id'])

    def purge(self, **kwargs):
        from .test_experiment_purge import purge_test_experiment
        return purge_test_experiment(experiment_id=self.experiment.pk, actor=self.teacher, **kwargs)

    def test_default_preview_does_not_change_records(self):
        before = AuditLog.objects.count()
        result = self.purge()
        self.assertFalse(result['applied'])
        self.assertEqual(result['counts']['assessment.ExperimentSession'], 1)
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertTrue(SubmissionEvent.objects.filter(attempt__session=self.session).exists())
        self.assertEqual(AuditLog.objects.count(), before)

    def test_apply_preserves_shared_resources_and_other_experiment(self):
        user_ids = set(User.objects.values_list('pk', flat=True))
        question_ids = set(Question.objects.values_list('pk', flat=True))
        enrollment_ids = set(CourseEnrollment.objects.values_list('pk', flat=True))
        snapshot = ExportSnapshot.objects.create(course=self.course, experiment=self.experiment, created_by=self.teacher,
            selected_versions=[{'id': self.session.version_id, 'version': 1}], source_bundle={'test': 'raw'})
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher)
        other_snapshot = ExportSnapshot.objects.create(course=self.course, experiment=self.other_experiment, created_by=self.teacher,
            selected_versions=[{'id': self.other_session.version_id, 'version': 1}], source_bundle={'keep': True})
        materialization = ResearchSessionMaterialization.objects.create(session=self.session)
        ResearchSessionResponse.objects.create(materialization=materialization, ordinal=1)
        ResearchSessionVisit.objects.create(materialization=materialization, ordinal=1)
        ResearchSessionBehavior.objects.create(materialization=materialization, ordinal=1)
        with self.captureOnCommitCallbacks(execute=True):
            result = self.purge(apply=True)
        self.assertTrue(result['applied'])
        self.assertFalse(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertFalse(ResearchSessionMaterialization.objects.filter(pk=materialization.pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=self.other_session.pk).exists())
        self.assertEqual(set(User.objects.values_list('pk', flat=True)), user_ids)
        self.assertEqual(set(Question.objects.values_list('pk', flat=True)), question_ids)
        self.assertEqual(set(CourseEnrollment.objects.values_list('pk', flat=True)), enrollment_ids)
        snapshot.refresh_from_db(); job.refresh_from_db(); other_snapshot.refresh_from_db()
        self.assertIsNone(snapshot.source_bundle)
        self.assertEqual(job.status, 'invalidated')
        self.assertEqual(other_snapshot.source_bundle, {'keep': True})
        self.assertTrue(AuditLog.objects.filter(action='experiment.test_purged', object_id=str(self.experiment.pk)).exists())

    def test_wrong_named_experiment_is_rejected(self):
        self.experiment = self.other_experiment
        with self.assertRaises(ValidationError):
            self.purge(apply=True)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())

    def test_failure_rolls_back_deletion_and_no_artifact_callback_runs(self):
        with patch('apps.audit.models.AuditLog.objects.create', side_effect=RuntimeError('audit storage unavailable')), patch('apps.experiments.test_experiment_purge._remove_artifacts') as cleanup:
            with self.assertRaises(RuntimeError):
                with self.captureOnCommitCallbacks(execute=True):
                    self.purge(apply=True)
            cleanup.assert_not_called()
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertTrue(SubmissionEvent.objects.filter(attempt__session=self.session).exists())

    def test_external_publish_plan_reference_blocks_the_entire_purge(self):
        target_plan = PublishPlan.objects.get(experiment=self.experiment)
        PublishPlan.objects.filter(experiment=self.other_experiment).update(source_plan=target_plan)
        with self.assertRaisesMessage(ValidationError, '清理范围外'):
            self.purge(apply=True)
        self.assertTrue(ExperimentSession.objects.filter(pk=self.session.pk).exists())
        self.assertTrue(ExperimentSession.objects.filter(pk=self.other_session.pk).exists())
