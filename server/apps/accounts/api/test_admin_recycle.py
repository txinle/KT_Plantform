from datetime import timedelta
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIClient
from apps.api.test_support import domain_payload
from apps.accounts.models import Course, ClassGroup, CourseEnrollment, User
from apps.question_bank.models import QuestionBank
from apps.experiments.models import Experiment


class AdminRecycleTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='recycle-admin', role='admin')
        self.teacher = User.objects.create_user(username='recycle-teacher', role='teacher')
        self.student = User.objects.create_user(username='recycle-student', role='student')
        self.course = Course.objects.create(name='恢复课程', code='RC', term='2026', owner=self.teacher)
        self.group = ClassGroup.objects.create(name='恢复班级', course=self.course, owner=self.teacher)
        self.enrollment = CourseEnrollment.objects.create(course=self.course, class_group=self.group, student=self.student)
        self.bank = QuestionBank.objects.create(name='恢复题库', course=self.course, owner=self.teacher)
        self.experiment = Experiment.objects.create(name='恢复实验', course=self.course, owner=self.teacher, question_bank=self.bank)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def delete(self, kind, obj):
        base = f'/api/v1/manage/{kind}/{obj.pk}/'
        preview = self.client.post(base+'delete-preview/', {}, format='json')
        self.assertEqual(preview.status_code, 200, preview.data)
        body = {'confirmation': 'DELETE', 'confirmation_name': domain_payload(preview)['name'],
                'confirmation_token': domain_payload(preview)['confirmation_token']}
        result = self.client.post(base+'delete/', body, format='json')
        self.assertEqual(result.status_code, 200, result.data)
        return domain_payload(result), base, body

    def test_course_delete_preserves_tree_hides_access_and_restores_same_ids(self):
        result, base, body = self.delete('courses', self.course)
        self.assertTrue(result['recoverable'])
        self.assertFalse(Course.objects.filter(pk=self.course.pk).exists())
        self.assertTrue(Experiment.objects.filter(pk=self.experiment.pk).exists())
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        resources = domain_payload(self.client.get('/api/v1/manage/resources/'))
        self.assertNotIn(self.bank.pk, [r['id'] for r in resources['banks']])
        again = domain_payload(self.client.post(base+'delete/', body, format='json'))
        self.assertEqual(again['recycle_id'], result['recycle_id'])
        self.client.force_authenticate(self.student)
        self.assertEqual(domain_payload(self.client.get('/api/v1/student/courses/'))['items'], [])
        self.client.force_authenticate(self.admin)
        restored = self.client.post(f"/api/v1/manage/recycle/{result['recycle_id']}/restore/", {}, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.assertEqual(Course.objects.get(pk=self.course.pk).status, 'active')
        self.assertEqual(QuestionBank.objects.get(pk=self.bank.pk).course_id, self.course.pk)

    def test_user_delete_disables_login_retains_membership_and_restores_prior_status(self):
        self.student.is_active = False
        self.student.save()
        result, _, _ = self.delete('users', self.student)
        self.student.refresh_from_db()
        self.assertIsNotNone(self.student.deleted_at)
        self.assertFalse(self.student.is_active)
        self.assertTrue(CourseEnrollment.objects.filter(pk=self.enrollment.pk).exists())
        self.assertNotIn(self.student.pk, [r['id'] for r in domain_payload(self.client.get('/api/v1/manage/users/'))['items']])
        enabled = self.client.post(f'/api/v1/manage/users/{self.student.pk}/status/', {'is_active': True}, format='json')
        self.assertEqual(enabled.status_code, 404)
        restored = self.client.post(f"/api/v1/manage/recycle/{result['recycle_id']}/restore/", {}, format='json')
        self.assertEqual(restored.status_code, 200, restored.data)
        self.student.refresh_from_db()
        self.assertIsNone(self.student.deleted_at)
        self.assertFalse(self.student.is_active)

    def test_self_delete_denied_and_expired_restore_denied(self):
        response = self.client.post(f'/api/v1/manage/users/{self.admin.pk}/delete-preview/', {}, format='json')
        self.assertEqual(response.status_code, 400)
        result, _, _ = self.delete('users', self.teacher)
        from apps.accounts.models import AdminRecycleEntry
        AdminRecycleEntry.objects.filter(pk=result['recycle_id']).update(restore_until=timezone.now()-timedelta(seconds=1))
        response = self.client.post(f"/api/v1/manage/recycle/{result['recycle_id']}/restore/", {}, format='json')
        self.assertEqual(response.status_code, 409)

    def test_teacher_cannot_restore(self):
        result, _, _ = self.delete('users', self.student)
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get('/api/v1/manage/recycle/').status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/manage/recycle/{result['recycle_id']}/restore/", {}, format='json').status_code, 403)

    def test_audit_failure_rolls_back_and_old_delete_retry_does_not_redelete(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from apps.accounts import admin_recycle
        preview = admin_recycle.preview('course', self.course.pk, self.admin)
        body = {'confirmation': 'DELETE', 'confirmation_name': self.course.name, 'confirmation_token': preview['confirmation_token']}
        with patch('apps.accounts.admin_recycle.log_action', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                admin_recycle.remove(SimpleNamespace(user=self.admin, META={}), 'course', self.course.pk, body)
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())
        result, base, body = self.delete('courses', self.course)
        self.client.post(f"/api/v1/manage/recycle/{result['recycle_id']}/restore/", {}, format='json')
        retry = self.client.post(base+'delete/', body, format='json')
        self.assertTrue(domain_payload(retry)['restored'])
        self.assertTrue(Course.objects.filter(pk=self.course.pk).exists())


from django.test import TransactionTestCase
from apps.experiments import test_research_deletion as fixtures


class ActiveCourseRecycleTests(TransactionTestCase):
    publish = fixtures.ResearchDeletionTests.publish
    export = fixtures.ResearchDeletionTests.export

    def setUp(self):
        fixtures.ResearchDeletionTests.setUp(self)
        self.admin = User.objects.create_user(username='recycle-admin-active', role='admin')

    def test_open_tasks_and_raw_responses_survive_delete_restore(self):
        from types import SimpleNamespace
        from apps.accounts import admin_recycle
        from apps.assessment.models import ResponseSnapshot, ExperimentSession
        from apps.assessment.services import _validate_course_access
        from django.core.exceptions import ValidationError
        from apps.experiments.models import ClassExperimentTask
        job, path = self.export([self.experiment])
        original = list(ResponseSnapshot.objects.values_list('pk', 'content'))
        task_ids = list(ClassExperimentTask.objects.values_list('pk', flat=True))
        preview = admin_recycle.preview('course', self.course.pk, self.admin)
        self.assertTrue(preview['allowed'])
        request = SimpleNamespace(user=self.admin, META={})
        result = admin_recycle.remove(request, 'course', self.course.pk,
            {'confirmation': 'DELETE', 'confirmation_name': self.course.name, 'confirmation_token': preview['confirmation_token']})
        with self.assertRaises(ValidationError):
            _validate_course_access(ExperimentSession.objects.get(pk=self.sessions[0].pk))
        self.assertTrue(path.exists())
        admin_recycle.restore(request, result['recycle_id'])
        self.assertEqual(list(ResponseSnapshot.objects.values_list('pk', 'content')), original)
        self.assertEqual(list(ClassExperimentTask.objects.values_list('pk', flat=True)), task_ids)
        _validate_course_access(ExperimentSession.objects.get(pk=self.sessions[0].pk))
        self.assertTrue(path.exists())
