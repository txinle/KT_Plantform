from types import SimpleNamespace
from django.core.exceptions import PermissionDenied
from django.test import TestCase
from django.utils import timezone
from apps.api.test_support import domain_payload
from apps.accounts.models import ClassGroup, Course, CourseTeacher, User
from apps.accounts.student_identity import _lock_context, StudentIdentityError
from apps.accounts.student_analytics_policy import update_class_student_analytics_policy
from apps.experiments.models import Experiment, PublishPlan


class CollaborationBoundaryTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='boundary-owner', role='teacher')
        self.member = User.objects.create_user(username='boundary-member', role='teacher')
        self.outsider = User.objects.create_user(username='boundary-outsider', role='teacher')
        self.course = Course.objects.create(owner=self.owner, name='权限边界', code='BOUND', term='2026')
        self.group = ClassGroup.objects.create(owner=self.owner, course=self.course, name='一班')
        CourseTeacher.objects.create(course=self.course, teacher=self.member, permissions=['bank'])
        self.client.force_login(self.member)

    def test_preview_download_requires_experiment_permission(self):
        experiment = Experiment.objects.create(owner=self.owner, course=self.course, name='发布计划')
        plan = PublishPlan.objects.create(experiment=experiment, created_by=self.owner, last_meaningful_write_at=timezone.now(), expires_at=timezone.now())
        response = self.client.get(f'/api/v1/teacher/publish-plans/{plan.pk}/preview/export/')
        self.assertIn(response.status_code, (403, 404))

    def test_identity_transaction_rechecks_class_permission(self):
        with self.assertRaises(StudentIdentityError):
            _lock_context(self.course.pk, self.group.pk, SimpleNamespace(user=self.member))

    def test_policy_transaction_rechecks_class_permission(self):
        with self.assertRaises(PermissionDenied):
            update_class_student_analytics_policy(class_group=self.group, actor=self.member, changes={'show_scores': False})

    def test_read_only_settings_do_not_leak_teacher_directory_or_offer_restore(self):
        settings = domain_payload(self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/settings/'))
        self.assertNotIn(self.outsider.pk, [item['id'] for item in settings['form']['teacher_options']])
        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=['status'])
        item = domain_payload(self.client.get('/api/v1/teacher/courses/'))['items'][0]
        self.assertIsNone(item['restore_url'])
