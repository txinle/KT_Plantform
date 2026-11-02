from unittest.mock import patch

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, CourseEnrollmentAccessPeriod, CourseSubject, User
from apps.api.test_support import domain_payload
from apps.assessment.models import ExperimentSession
from apps.experiments.models import ClassExperimentTask, Experiment, ExperimentClassSnapshot, ExperimentVersion, ExperimentVersionParticipantSnapshot
from apps.experiments.services import _class_experiment_task_publication_scope

from .admin_workspace_views import task_rows


class AdminStudentCreationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='student-create-admin', role='admin')
        self.course = Course.objects.create(name='课程', code='STUDENT', term='2026', owner=self.admin)
        self.group = ClassGroup.objects.create(course=self.course, owner=self.admin, name='班级')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)
        self.body = {'role': 'student', 'student_number': 'S001', 'name': '学生',
                     'course_id': self.course.pk, 'class_group_id': self.group.pk}

    def test_creation_requires_both_course_and_class(self):
        for field in ('course_id', 'class_group_id'):
            with self.subTest(field=field):
                body = {key: value for key, value in self.body.items() if key != field}
                response = self.client.post('/api/v1/manage/users/', body, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(User.objects.filter(student_number='S001').exists())

    def test_creation_opens_enrollment_and_preserves_initial_password_contract(self):
        response = self.client.post('/api/v1/manage/users/', {**self.body, 'email': 'student@example.com'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        student = User.objects.get(student_number='S001')
        enrollment = CourseEnrollment.objects.get(student=student, course=self.course, class_group=self.group, is_active=True)
        self.assertTrue(CourseSubject.objects.filter(student=student, course=self.course).exists())
        self.assertTrue(CourseEnrollmentAccessPeriod.objects.filter(enrollment=enrollment, revoked_at__isnull=True).exists())
        self.assertTrue(student.check_password('SS001'))
        self.assertTrue(student.initial_password_reminder_pending)
        self.assertFalse(student.must_change_password)
        self.assertEqual(student.email, 'student@example.com')
        self.assertEqual(domain_payload(response)['course_count'], 1)

    def test_inactive_or_foreign_class_and_archived_course_are_rejected_atomically(self):
        other = Course.objects.create(name='其他课程', code='OTHER', term='2026', owner=self.admin)
        foreign = ClassGroup.objects.create(course=other, owner=self.admin, name='其他班级')
        inactive = ClassGroup.objects.create(course=self.course, owner=self.admin, name='停用班级', is_active=False)
        archived = Course.objects.create(name='归档课程', code='ARCHIVED', term='2026', owner=self.admin, status='archived')
        archived_group = ClassGroup.objects.create(course=archived, owner=self.admin, name='归档班级')
        for course, group in ((self.course, foreign), (self.course, inactive), (archived, archived_group)):
            with self.subTest(group=group.pk):
                response = self.client.post('/api/v1/manage/users/', {**self.body, 'course_id': course.pk, 'class_group_id': group.pk}, format='json')
                self.assertEqual(response.status_code, 400, response.data)
                self.assertFalse(User.objects.filter(student_number='S001').exists())

    def test_existing_account_is_not_silently_enrolled_by_account_creation(self):
        student = User.objects.create_user(username='S001', student_number='S001', display_name='学生', role='student')
        response = self.client.post('/api/v1/manage/users/', self.body, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        self.assertFalse(CourseEnrollment.objects.filter(student=student).exists())

    def test_recycled_account_cannot_be_reenabled_by_status_endpoint(self):
        student = User.objects.create_user(username='recycled-student', role='student', is_active=False, deleted_at=timezone.now())
        response = self.client.post(f'/api/v1/manage/users/{student.pk}/status/', {'is_active': True}, format='json')
        self.assertEqual(response.status_code, 404, response.data)
        student.refresh_from_db()
        self.assertFalse(student.is_active)

    def test_audit_failure_rolls_back_account_and_membership(self):
        with patch('apps.accounts.admin_workspace.log_action', side_effect=RuntimeError('audit failed')):
            with self.assertRaises(RuntimeError):
                self.client.post('/api/v1/manage/users/', self.body, format='json')
        self.assertFalse(User.objects.filter(student_number='S001').exists())
        self.assertFalse(CourseEnrollment.objects.exists())
        self.assertFalse(CourseSubject.objects.exists())
        self.assertFalse(CourseEnrollmentAccessPeriod.objects.exists())


class AdminOverviewTaskTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='overview-admin', role='admin')
        self.course = Course.objects.create(name='概览课程', code='OVERVIEW', term='2026', owner=self.admin)
        self.experiment = Experiment.objects.create(course=self.course, name='实验', owner=self.admin)
        self.version = ExperimentVersion.objects.create(experiment=self.experiment, version_number=1, published_by=self.admin)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def create_task(self, index):
        group = ClassGroup.objects.create(course=self.course, name=f'班级{index}')
        snapshot = ExperimentClassSnapshot.objects.create(version=self.version, source_class_group=group, name_snapshot=group.name)
        with _class_experiment_task_publication_scope():
            return ClassExperimentTask.objects.create(class_group=group, experiment=self.experiment,
                release_snapshot=self.version, class_snapshot=snapshot, published_at=self.version.published_at)

    def test_overview_only_projects_latest_four_tasks(self):
        tasks = [self.create_task(index) for index in range(6)]
        response = self.client.get('/api/v1/manage/overview/')
        self.assertEqual(response.status_code, 200, response.data)
        data = domain_payload(response)
        self.assertEqual([row['id'] for row in data['tasks']], [task.pk for task in reversed(tasks[-4:])])
        self.assertEqual(data['tasks_scope'], 'latest')
        self.assertEqual(data['tasks_limit'], 4)

    def test_task_projection_queries_do_not_grow_per_automatic_task(self):
        first = self.create_task(0)
        for index, status in enumerate(('prepared', 'in_progress', 'completed')):
            student = User.objects.create_user(username=f'participant-{index}', role='student')
            participant = ExperimentVersionParticipantSnapshot.objects.create(version=self.version, class_task=first, student=student)
            ExperimentSession.objects.create(version=self.version, class_task=first, participant_snapshot=participant, student=student,
                status=status, started_at=None if status == 'prepared' else timezone.now())
        with CaptureQueriesContext(connection) as one:
            rows = task_rows()
        self.assertEqual((rows[0]['participant_count'], rows[0]['started_count'], rows[0]['submitted_count']), (3, 2, 1))
        for index in range(1, 6):
            self.create_task(index)
        with CaptureQueriesContext(connection) as many:
            rows = task_rows()
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(many), len(one), f'one={len(one)}, six={len(many)}')
        self.assertLessEqual(len(many), 6)
