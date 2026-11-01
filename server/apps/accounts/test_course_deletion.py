from types import SimpleNamespace
from django.test import TestCase
from apps.accounts.models import Course, ClassGroup, CourseEnrollment, User
from apps.question_bank.models import QuestionBank, Question
from apps.experiments.models import Experiment, ResearchDeletionOperation
from apps.accounts.course_deletion import preview_course_deletion, execute_course_deletion, recover_course_deletion
from unittest.mock import patch
from apps.experiments.research_deletion import DeletionConflict
from django.test import TransactionTestCase
from apps.experiments import test_research_deletion as deletion_fixtures
from apps.experiments.publication_services import manual_close_task
from apps.analytics.models import ExportSnapshot, ExportJob, ResearchArtifactCleanupJob
from apps.assessment.models import ExperimentSession
from django.db import close_old_connections, transaction
from threading import Event, Thread
from pathlib import Path
from apps.analytics.models import ResearchExportAsset
from apps.analytics.research_deletion import process_artifact_cleanup


class CourseDeletionTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='admin-delete', role='admin')
        self.teacher = User.objects.create_user(username='owner-delete', role='teacher')
        self.student = User.objects.create_user(username='student-delete', role='student', student_number='DELETE001')
        self.course = Course.objects.create(owner=self.teacher, name='Delete me', code='D', term='2026')
        self.other = Course.objects.create(owner=self.teacher, name='Keep me', code='K', term='2026')
        self.group = ClassGroup.objects.create(course=self.course, owner=self.teacher, name='Class')
        CourseEnrollment.objects.create(course=self.course, class_group=self.group, student=self.student)
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Bank')
        self.experiment = Experiment.objects.create(course=self.course, owner=self.teacher, question_bank=self.bank, name='Draft')
        self.request = SimpleNamespace(user=self.admin, META={})

    def test_delete_draft_closure_preserves_accounts_and_other_course(self):
        preview = preview_course_deletion(self.course, self.admin)
        self.assertTrue(preview['allowed'])
        result = execute_course_deletion(self.request, self.course, preview['confirmation_token'], self.course.name)
        self.assertTrue(result['deleted'])
        self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(Course.objects.filter(pk=self.other.pk).exists())
        self.assertEqual(User.objects.count(), 3)
        receipt = ResearchDeletionOperation.objects.get(pk=result['operation_id'])
        self.assertIsNone(receipt.course_id)
        self.assertEqual(receipt.frozen_scope['course_id'], self.course.pk)
        self.assertEqual(recover_course_deletion(self.course.pk, self.admin, preview['confirmation_token'])['operation_id'], result['operation_id'])
        self.assertIsNone(recover_course_deletion(self.course.pk, self.admin, 'unrelated token'))

    def test_changed_scope_and_wrong_name_do_not_delete(self):
        preview = preview_course_deletion(self.course, self.admin)
        with self.assertRaises(DeletionConflict):
            execute_course_deletion(self.request, self.course, preview['confirmation_token'], 'Wrong')
        self.bank.name = 'Changed'
        self.bank.save()
        with self.assertRaises(DeletionConflict):
            execute_course_deletion(self.request, self.course, preview['confirmation_token'], self.course.name)
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())

    def test_cross_course_bank_reference_blocks(self):
        Experiment.objects.create(course=self.other, owner=self.teacher, question_bank=self.bank, name='Other experiment')
        self.assertFalse(preview_course_deletion(self.course, self.admin)['allowed'])

    def test_late_failure_rolls_back_complete_tree(self):
        preview = preview_course_deletion(self.course, self.admin)
        with patch('apps.accounts.course_deletion.log_action', side_effect=RuntimeError('audit unavailable')):
            with self.assertRaises(RuntimeError):
                execute_course_deletion(self.request, self.course, preview['confirmation_token'], self.course.name)
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertTrue(QuestionBank.objects.filter(pk=self.bank.pk).exists())
        self.assertFalse(ResearchDeletionOperation.objects.exists())


class PublishedCourseDeletionTests(TransactionTestCase):
    publish = deletion_fixtures.ResearchDeletionTests.publish

    def setUp(self):
        deletion_fixtures.ResearchDeletionTests.setUp(self)
        self.admin = User.objects.create_user(username='admin-delete-published', role='admin')
        self.request = SimpleNamespace(user=self.admin, META={})

    def test_open_task_block_then_closed_task_full_deletion_and_cleanup_receipt(self):
        self.assertFalse(preview_course_deletion(self.course, self.admin)['allowed'])
        for task in self.tasks:
            manual_close_task(task, self.teacher, reason='研究结束')
        snapshot = ExportSnapshot.objects.create(course=self.course, experiment=self.experiment, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, experiment=self.experiment, snapshot=snapshot, created_by=self.teacher, package='test', status='succeeded', artifact_path=str(self.media.name + '/exports/test.zip'))
        asset_path = Path(self.media.name) / 'research-assets' / 'test.zip'
        asset_path.parent.mkdir(parents=True)
        asset_path.write_bytes(b'test asset')
        ResearchExportAsset.objects.create(course=self.course, experiment=self.experiment, kind='experiment_sessions', content_hash='abc', artifact_path=str(asset_path), artifact_sha256='abc')
        preview = preview_course_deletion(self.course, self.admin)
        self.assertTrue(preview['allowed'], preview['blocked_reason'])
        result = execute_course_deletion(self.request, self.course, preview['confirmation_token'], self.course.name)
        self.assertFalse(ExperimentSession.objects.filter(pk__in=[s.pk for s in self.sessions]).exists())
        self.assertFalse(ExportJob.objects.filter(pk=job.pk).exists())
        self.assertTrue(User.objects.filter(pk=self.student.pk).exists())
        self.assertTrue(result['cleanup_pending'])
        self.assertTrue(ResearchArtifactCleanupJob.objects.filter(cleanup__purge_request_id=result['cleanup_id']).exists())
        self.assertTrue(asset_path.exists())
        self.assertTrue(process_artifact_cleanup(ResearchArtifactCleanupJob.objects.get(cleanup__purge_request_id=result['cleanup_id']).cleanup_id))
        self.assertFalse(asset_path.exists())

    def test_concurrent_course_writer_blocks_deletion_without_partial_changes(self):
        for task in self.tasks:
            manual_close_task(task, self.teacher, reason='研究结束')
        preview = preview_course_deletion(self.course, self.admin)
        locked, release = Event(), Event()
        def writer():
            close_old_connections()
            try:
                with transaction.atomic():
                    Course.objects.select_for_update().get(pk=self.course.pk)
                    locked.set()
                    release.wait(10)
            finally:
                close_old_connections()
        thread = Thread(target=writer)
        thread.start()
        try:
            self.assertTrue(locked.wait(5))
            with self.assertRaises(DeletionConflict):
                execute_course_deletion(self.request, self.course, preview['confirmation_token'], self.course.name)
        finally:
            release.set()
            thread.join(10)
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
        self.assertEqual(ExperimentSession.objects.filter(pk__in=[s.pk for s in self.sessions]).count(), 2)
