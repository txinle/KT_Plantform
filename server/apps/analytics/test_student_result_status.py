from apps.api.test_support import domain_payload
from unittest.mock import patch

from django.db import connection
from django.test import TransactionTestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils.dateparse import parse_datetime

from apps.accounts.models import CourseEnrollment
from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.assessment.models import ExperimentSession


@override_settings(STUDENT_SHARED_RESULTS_ENABLED=True,
                   STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}),
                   CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True)
class StudentResultStatusTests(StudentApiFixtureMixin, TransactionTestCase):
    def setUp(self):
        super().setUp()
        response = self.start()
        self.session = ExperimentSession.objects.get(pk=domain_payload(response)['session_id'])
        self.complete_session(self.session)
        self.base = f'/api/v1/student/courses/{self.course.pk}/learning/'
        self.detail = self.base + f'experiments/{self.experiment.pk}/'
        self.task_url = f'/api/v1/student/tasks/{self.task.pk}/results/'

    def test_metadata_never_loads_result_facts_or_cohort_payload(self):
        from apps.analytics.student_comparisons import run_comparison_jobs
        run_comparison_jobs(limit=1)
        snapshot = self.session.attempts.first().assignment.snapshot
        urls = [self.base, self.detail, self.detail + f'questions/{snapshot.pk}/', self.task_url]
        with patch('apps.analytics.teaching.student_projection._load_http_bundle', side_effect=AssertionError('full facts')), \
             patch('apps.analytics.student_result_snapshots.get_student_experiment_result_payload', side_effect=AssertionError('snapshot')), \
             patch('apps.assessment.api.views.build_student_task_result', side_effect=AssertionError('task facts')):
            for url in urls:
                with self.subTest(url=url), CaptureQueriesContext(connection) as queries:
                    response = self.client.get(url, {'status_only': '1'})
                    self.assertEqual(response.status_code, 200, response.content)
                    self.assertIn('version', domain_payload(response))
                    self.assertLess(len(response.content), 500)
                sql = '\n'.join(q['sql'] for q in queries).lower()
                self.assertNotIn('"final_answer"', sql)
                self.assertNotIn('"analytics_studentclasstaskcomparison"."payload"', sql)
                self.assertNotIn('"analytics_studentclasslearningcomparison"."payload"', sql)
                self.assertNotIn('assessment_graderecord', sql)
                if url == self.base:
                    self.assertIn("'task_signatures'", sql)
                    self.assertNotIn('"own"', sql)
                writes = [q['sql'].lower() for q in queries if q['sql'].lower().startswith(('update ', 'insert into'))]
                self.assertTrue(all('django_session' in q for q in writes), writes)
        self.assertEqual(parse_datetime(domain_payload(self.client.get(self.task_url, {'status_only': '1'}))['completed_at']), self.session.terminal_at)

    def test_ready_metadata_tracks_worker_publication_without_fact_rebuild(self):
        from apps.analytics.student_comparisons import run_comparison_jobs
        from apps.analytics.models import StudentClassLearningComparison
        before = domain_payload(self.client.get(self.detail, {'status_only': '1'}))
        self.assertEqual(before['comparison_status'], 'pending')
        run_comparison_jobs(limit=1)
        ready = domain_payload(self.client.get(self.detail, {'status_only': '1'}))
        self.assertEqual(ready['comparison_status'], 'ready')
        self.assertIn('comparison_generated_at', ready)
        self.assertNotEqual(before['version'], ready['version'])
        course = domain_payload(self.client.get(self.base, {'status_only': '1'}))
        self.assertEqual(course['comparison_status'], 'ready')
        from django.utils import timezone
        StudentClassLearningComparison.objects.filter(class_group=self.class_group).update(generated_at=timezone.now())
        self.assertEqual(course['version'], domain_payload(self.client.get(self.base, {'status_only': '1'}))['version'])

    def test_question_metadata_preserves_item_policy_and_own_submission_scope(self):
        snapshot = self.session.attempts.first().assignment.snapshot
        url = self.detail + f'questions/{snapshot.pk}/'
        self.assertEqual(self.client.get(url, {'status_only': '1'}).status_code, 200)
        self.assertEqual(self.client.get(self.detail + 'questions/999999/', {'status_only': '1'}).status_code, 404)
        policy = self.class_group.student_analytics_policy
        policy.show_item_results = False
        policy.save()
        self.assertEqual(self.client.get(url, {'status_only': '1'}, HTTP_IF_NONE_MATCH='*').status_code, 403)

    def test_full_result_does_not_reuse_version_when_publication_state_changed_between_reads(self):
        for url in (self.base, self.detail, self.task_url):
            with self.subTest(url=url), patch('apps.assessment.api.views._student_status', return_value={
                'version': 'previous-ready', 'comparison_status': 'ready',
            }):
                full = self.client.get(url)
                self.assertEqual(full.status_code, 200, full.content)
                self.assertEqual(domain_payload(full)['comparison_status'], 'pending')
                self.assertNotIn('version', domain_payload(full))

    def test_version_matches_full_result_and_changes_on_own_result_revision(self):
        for url in [self.base, self.detail, self.task_url]:
            with self.subTest(url=url):
                full = self.client.get(url)
                status = self.client.get(url, {'status_only': '1'})
                self.assertEqual(domain_payload(full)['version'], domain_payload(status)['version'])
                self.session.result_revision += 1
                self.session.save(update_fields=['result_revision'])
                self.assertNotEqual(domain_payload(status)['version'], domain_payload(self.client.get(url, {'status_only': '1'}))['version'])

    def test_metadata_conditional_response_rechecks_live_enrollment_and_feedback(self):
        response = self.client.get(self.detail, {'status_only': '1'})
        etag = response['ETag']
        self.assertEqual(self.client.get(self.detail, {'status_only': '1'}, HTTP_IF_NONE_MATCH=etag).status_code, 304)
        self.session.status = self.session.Status.IN_PROGRESS
        self.session.terminal_at = None
        self.session.save(update_fields=['status', 'terminal_at'])
        self.assertEqual(self.client.get(self.detail, {'status_only': '1'}, HTTP_IF_NONE_MATCH=etag).status_code, 403)
        CourseEnrollment.objects.filter(course=self.course, student=self.student).update(is_active=False)
        self.assertEqual(self.client.get(self.detail, {'status_only': '1'}, HTTP_IF_NONE_MATCH=etag).status_code, 404)
