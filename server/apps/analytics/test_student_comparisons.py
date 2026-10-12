from apps.api.test_support import domain_payload
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings
from django.db import connection
from django.test.utils import CaptureQueriesContext

from apps.assessment.test_student_api import StudentApiFixtureMixin
from apps.assessment.models import ExperimentSession, SubmissionEvent
from apps.assessment.services import grade_submission
from apps.accounts.models import CourseEnrollment
from apps.analytics.models import StudentExperimentResultSnapshot


@override_settings(CLASS_CENTER_ENABLED=True, STUDENT_LEARNING_ANALYTICS_ENABLED=True, STUDENT_SHARED_RESULTS_ENABLED=True, STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'*'}))
class StudentComparisonIsolationTests(StudentApiFixtureMixin, TransactionTestCase):
    def _prepare(self):
        session = ExperimentSession.objects.get(pk=domain_payload(self.start())['session_id'])
        self.complete_session(session)
        return session

    def _url(self):
        return f'/api/v1/student/courses/{self.course.pk}/learning/experiments/{self.experiment.pk}/'

    def test_old_session_insert_can_omit_result_revision(self):
        session = ExperimentSession.objects.get(pk=domain_payload(self.start())['session_id'])
        fields = [field for field in ExperimentSession._meta.concrete_fields
                  if not field.primary_key and field.name != 'result_revision']
        quote = connection.ops.quote_name
        columns = ', '.join(quote(field.column) for field in fields)
        replacements = {'student_id': self.outsider.pk,
                        'class_task_id': None, 'participant_snapshot_id': None}
        expressions = [('%s' if field.column in replacements else quote(field.column))
                       for field in fields]
        values = [replacements[field.column] for field in fields
                  if field.column in replacements]
        table = quote(ExperimentSession._meta.db_table)
        with connection.cursor() as cursor:
            cursor.execute(
                f'INSERT INTO {table} ({columns}) '
                f'SELECT {", ".join(expressions)} FROM {table} WHERE id = %s '
                'RETURNING result_revision', values + [session.pk])
            self.assertEqual(cursor.fetchone()[0], 0)

    def test_cold_personal_result_does_not_load_cohort_bundle(self):
        self._prepare()
        with patch('apps.analytics.teaching.student_projection._load_bundle', side_effect=AssertionError('cohort bundle forbidden')):
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['comparison_status'], 'pending')
        self.assertEqual(len(domain_payload(response)['item_results']), 1)

    def test_raw_revision_does_not_invalidate_personal_snapshot(self):
        session = self._prepare()
        self.client.get(self._url())
        cached = StudentExperimentResultSnapshot.objects.get(student=self.student, class_task=self.task)
        original_signature = cached.source_signature
        session.research_source_revision += 1
        session.save(update_fields=['research_source_revision'])
        self.client.get(self._url())
        cached.refresh_from_db()
        self.assertEqual(cached.source_signature, original_signature)
        with patch('apps.analytics.teaching.student_projection._build_student_experiment_projection', side_effect=AssertionError('personal snapshot rebuilt')):
            self.assertEqual(self.client.get(self._url()).status_code, 200)

    def test_grading_advances_only_result_facts_transactionally(self):
        session = self._prepare()
        before = getattr(session, 'result_revision', 0)
        grade_submission(SubmissionEvent.objects.get(attempt__session=session).pk, score=0, max_score=1, is_correct=False)
        session.refresh_from_db()
        self.assertGreater(session.result_revision, before)

    def test_worker_builds_shared_summary_and_membership_invalidates_it(self):
        self._prepare()
        from apps.analytics.student_comparisons import run_comparison_jobs
        from apps.analytics.models import StudentClassTaskComparison
        self.assertEqual(run_comparison_jobs(limit=1)['completed'], 1)
        response = self.client.get(self._url())
        self.assertEqual(domain_payload(response)['comparison_status'], 'ready')
        summary = StudentClassTaskComparison.objects.get(class_task=self.task)
        CourseEnrollment.objects.filter(course=self.course, student=self.peer).update(is_active=False)
        self.task.refresh_from_db()
        self.assertLess(summary.source_revision, self.task.result_revision)
        self.assertEqual(domain_payload(self.client.get(self._url()))['comparison_status'], 'updating')

    def test_http_summary_extracts_only_own_slice_and_global_aggregates(self):
        self._prepare()
        from apps.analytics.student_comparisons import run_comparison_jobs, read_comparisons
        run_comparison_jobs(limit=1)
        rows = read_comparisons((self.task,), student_id=self.student.pk)
        self.assertNotIn('students', rows[self.task.pk].payload)
        self.assertIn('means', rows[self.task.pk].payload)

    def test_task_read_never_references_cohort_payload_in_sql(self):
        self._prepare()
        from apps.analytics.student_comparisons import run_comparison_jobs, read_comparisons
        from apps.analytics.models import StudentClassTaskComparison
        run_comparison_jobs(limit=1)
        with CaptureQueriesContext(connection) as queries:
            rows = read_comparisons((self.task,), student_id=self.student.pk)
        self.assertEqual(len(queries), 1)
        self.assertIsNotNone(rows[self.task.pk].payload['own_rank'])
        self.assertNotIn(f'"{StudentClassTaskComparison._meta.db_table}"."payload"', queries[0]['sql'])
        self.assertIn('student_id', queries[0]['sql'])

    def test_learning_read_never_references_cohort_payload_in_sql(self):
        self._prepare()
        from apps.analytics.student_comparisons import run_comparison_jobs, read_learning_comparison
        from apps.analytics.models import StudentClassLearningComparison
        run_comparison_jobs(limit=1)
        with CaptureQueriesContext(connection) as queries:
            row = read_learning_comparison(self.class_group.pk, self.student.pk)
        self.assertEqual(len(queries), 1)
        self.assertIsNotNone(row['own'])
        self.assertNotIn(f'"{StudentClassLearningComparison._meta.db_table}"."payload"', queries[0]['sql'])
        self.assertIn('student_id', queries[0]['sql'])

    def test_old_ready_layout_is_pending_until_background_rebuild(self):
        self._prepare()
        from django.utils import timezone
        from apps.analytics.models import StudentClassTaskComparison
        from apps.analytics.student_comparisons import run_comparison_jobs
        self.task.refresh_from_db()
        StudentClassTaskComparison.objects.create(
            class_task=self.task, source_revision=self.task.result_revision,
            status='ready', generated_at=timezone.now(), payload={'students': {}},
        )
        with patch('apps.analytics.teaching.student_projection._load_bundle', side_effect=AssertionError('HTTP cohort scan')):
            response = self.client.get(self._url())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['comparison_status'], 'pending')
        self.assertEqual(len(domain_payload(response)['item_results']), 1)
        self.assertEqual(run_comparison_jobs(limit=1)['completed'], 1)
        self.assertEqual(domain_payload(self.client.get(self._url()))['comparison_status'], 'ready')

    def test_old_learning_layout_is_pending_until_background_rebuild(self):
        self._prepare()
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs
        run_comparison_jobs(limit=1)
        StudentClassLearningComparison.objects.filter(class_group=self.class_group).update(projection_version=0)
        url = f'/api/v1/student/courses/{self.course.pk}/learning/'
        with patch('apps.analytics.teaching.student_projection._load_bundle', side_effect=AssertionError('HTTP cohort scan')):
            self.assertEqual(domain_payload(self.client.get(url))['comparison_status'], 'pending')
        with patch('apps.analytics.student_comparisons._build_summary', side_effect=AssertionError('task facts rebuilt')):
            run_comparison_jobs(limit=1)
        self.assertEqual(domain_payload(self.client.get(url))['comparison_status'], 'ready')

    def test_task_publish_failure_rolls_back_aggregate_and_members_together(self):
        session = self._prepare()
        from apps.analytics.models import StudentClassTaskComparison, StudentTaskComparisonMember
        from apps.analytics.student_comparisons import run_comparison_jobs, read_comparisons, ready_payload
        run_comparison_jobs(limit=1)
        parent = StudentClassTaskComparison.objects.get(class_task=self.task)
        original_means = parent.means
        original_ranks = list(parent.members.order_by('student_id').values_list('student_id', 'rank'))
        grade_submission(SubmissionEvent.objects.get(attempt__session=session).pk, score=0, max_score=1, is_correct=False)
        bulk_create = StudentTaskComparisonMember.objects.bulk_create

        def interrupted_publish(*args, **kwargs):
            bulk_create(*args, **kwargs)
            raise RuntimeError('member publication interrupted')

        with patch.object(StudentTaskComparisonMember.objects, 'bulk_create', side_effect=interrupted_publish), self.assertLogs('apps.analytics.student_comparisons', level='ERROR'):
            run_comparison_jobs(limit=1)
        parent.refresh_from_db()
        self.assertEqual(parent.means, original_means)
        self.assertEqual(list(parent.members.order_by('student_id').values_list('student_id', 'rank')), original_ranks)
        self.task.refresh_from_db()
        self.assertIsNone(ready_payload(self.task, read_comparisons((self.task,), student_id=self.student.pk)))

    def test_learning_publish_failure_rolls_back_parent_and_members_together(self):
        self._prepare()
        from django.utils import timezone
        from apps.analytics.models import StudentClassLearningComparison, StudentLearningComparisonMember
        from apps.analytics.student_comparisons import run_comparison_jobs, read_learning_comparison, learning_payload
        run_comparison_jobs(limit=1)
        parent = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        original_date = parent.generated_at
        original_members = list(parent.members.order_by('student_id').values_list('student_id', 'payload'))
        grade_submission(SubmissionEvent.objects.get(attempt__session__student=self.student).pk, score=0, max_score=1, is_correct=False)
        parent.next_refresh_at = timezone.now()
        parent.save(update_fields=['next_refresh_at'])
        bulk_create = StudentLearningComparisonMember.objects.bulk_create

        def interrupted_publish(*args, **kwargs):
            bulk_create(*args, **kwargs)
            raise RuntimeError('member publication interrupted')

        with patch.object(StudentLearningComparisonMember.objects, 'bulk_create', side_effect=interrupted_publish), self.assertLogs('apps.analytics.student_comparisons', level='ERROR'):
            run_comparison_jobs(limit=1)
        parent.refresh_from_db()
        self.assertEqual(parent.generated_at, original_date)
        self.assertEqual(list(parent.members.order_by('student_id').values_list('student_id', 'payload')), original_members)
        self.task.refresh_from_db()
        self.assertIsNone(learning_payload((self.task,), read_learning_comparison(self.class_group.pk, self.student.pk)))

    def test_unchanged_ready_course_only_checks_scalar_dependencies(self):
        from django.utils import timezone
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs, _run_learning_comparison_jobs
        self._prepare()
        run_comparison_jobs(limit=1)
        row = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        original_date = row.generated_at
        original_members = list(row.members.order_by('pk').values_list('pk', 'student_id', 'payload'))
        StudentClassLearningComparison.objects.filter(pk=row.pk).update(next_refresh_at=timezone.now())
        with patch('apps.analytics.student_comparisons._refresh_learning_comparison', side_effect=AssertionError('unchanged cohort payload rebuilt')) as build, CaptureQueriesContext(connection) as queries:
            _run_learning_comparison_jobs(limit=1)
        build.assert_not_called()
        row.refresh_from_db()
        self.assertEqual(row.status, 'ready')
        self.assertEqual(row.generated_at, original_date)
        self.assertEqual(list(row.members.order_by('pk').values_list('pk', 'student_id', 'payload')), original_members)
        sql = '\n'.join(q['sql'].lower() for q in queries)
        self.assertNotIn('"analytics_studentclasstaskcomparison"."payload"', sql)
        self.assertNotIn('analytics_studentlearningcomparisonmember', sql)

    def test_course_clock_check_discovers_deadline_feedback_release_without_fact_revision(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.experiments.models import Experiment
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs, _run_learning_comparison_jobs
        now = timezone.now()
        experiment, task = self.publish_task(name='deadline-feedback', feedback_mode=Experiment.Feedback.FULL,
            feedback_release_policy='after_assignment_deadline', opens_at=now-timedelta(minutes=1), closes_at=now+timedelta(minutes=1))
        session = ExperimentSession.objects.get(pk=domain_payload(self.start(task=task))['session_id'])
        self.complete_session(session)
        run_comparison_jobs(limit=2)
        row = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        original_date = row.generated_at
        self.assertNotIn(str(task.pk), row.members.get(student=self.student).payload['task_signatures'])
        task.refresh_from_db()
        revision = task.result_revision
        future = now+timedelta(minutes=2)
        with patch('apps.analytics.student_comparisons.timezone.now', return_value=future):
            _run_learning_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertGreater(row.generated_at, original_date)
        self.assertIn(str(task.pk), row.members.get(student=self.student).payload['task_signatures'])
        task.refresh_from_db()
        self.assertEqual(task.result_revision, revision)

    def test_course_source_change_during_build_requeues_without_publishing_stale_members(self):
        from django.utils import timezone
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs, _run_learning_comparison_jobs, _refresh_learning_comparison
        self._prepare()
        run_comparison_jobs(limit=1)
        row = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        original_date = row.generated_at
        original_members = list(row.members.order_by('pk').values_list('pk', 'payload'))
        row.status = 'queued'
        row.payload.pop('source_signature', None)
        row.next_refresh_at = timezone.now()
        row.save(update_fields=['status', 'payload', 'next_refresh_at'])
        def raced_build(class_group_id):
            payload = _refresh_learning_comparison(class_group_id)
            from apps.experiments.models import ClassExperimentTask
            ClassExperimentTask.objects.filter(pk=self.task.pk).bump_result_revision()
            return payload
        with patch('apps.analytics.student_comparisons._refresh_learning_comparison', side_effect=raced_build):
            _run_learning_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'queued')
        self.assertEqual(row.generated_at, original_date)
        self.assertEqual(list(row.members.order_by('pk').values_list('pk', 'payload')), original_members)
        run_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'ready')

    def test_course_rebuilds_after_eligibility_or_schedule_revision_changes(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.experiments.models import ScheduleRevision
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs, _run_learning_comparison_jobs
        self._prepare()
        run_comparison_jobs(limit=1)
        row = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        original_signature = row.payload['source_signature']
        CourseEnrollment.objects.filter(student=self.peer, course=self.course).update(is_active=False)
        run_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertNotEqual(original_signature, row.payload['source_signature'])
        original_signature = row.payload['source_signature']
        ScheduleRevision.objects.create(class_task=self.task, revision_no=2,
            closes_at=timezone.now()+timedelta(days=1), created_by=self.teacher)
        StudentClassLearningComparison.objects.filter(pk=row.pk).update(next_refresh_at=timezone.now())
        _run_learning_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'ready')
        self.assertNotEqual(original_signature, row.payload['source_signature'])

    def test_learning_overview_reads_own_facts_and_background_course_slice(self):
        self._prepare()
        from apps.analytics.student_comparisons import run_comparison_jobs
        run_comparison_jobs(limit=1)
        with patch('apps.analytics.teaching.student_projection._load_bundle', side_effect=AssertionError('HTTP cohort scan')):
            response = self.client.get(f'/api/v1/student/courses/{self.course.pk}/learning/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['comparison_status'], 'ready')

    def test_expired_exhausted_lease_becomes_failed(self):
        from datetime import timedelta
        from django.utils import timezone
        from apps.analytics.models import StudentClassTaskComparison
        from apps.analytics.student_comparisons import run_comparison_jobs
        self._prepare()
        row = StudentClassTaskComparison.objects.create(class_task=self.task, status='running', attempts=3, started_at=timezone.now()-timedelta(minutes=3))
        run_comparison_jobs(limit=1)
        row.refresh_from_db()
        self.assertEqual(row.status, 'failed')

    def test_failed_generation_retries_three_times_then_stops(self):
        from django.utils import timezone
        from apps.analytics.models import StudentClassTaskComparison
        from apps.analytics.student_comparisons import run_comparison_jobs
        self._prepare()
        with patch('apps.analytics.student_comparisons._build_summary', side_effect=RuntimeError('injected failure')) as build:
            for _ in range(3):
                run_comparison_jobs(limit=1)
                StudentClassTaskComparison.objects.filter(class_task=self.task).update(next_attempt_at=timezone.now())
            run_comparison_jobs(limit=1)
        self.assertEqual(build.call_count, 3)
        self.assertEqual(StudentClassTaskComparison.objects.get(class_task=self.task).status, 'failed')

    def test_first_course_refresh_failure_is_retried_without_raw_rebuild(self):
        from django.utils import timezone
        from apps.analytics.models import StudentClassLearningComparison
        from apps.analytics.student_comparisons import run_comparison_jobs
        self._prepare()
        with patch('apps.analytics.student_comparisons._refresh_learning_comparison', side_effect=RuntimeError('course build interrupted')):
            run_comparison_jobs(limit=1)
        row = StudentClassLearningComparison.objects.get(class_group=self.class_group)
        self.assertEqual(row.status, 'queued')
        row.next_refresh_at = timezone.now()
        row.save(update_fields=['next_refresh_at'])
        with patch('apps.analytics.student_comparisons._build_summary', side_effect=AssertionError('raw task facts rebuilt')):
            run_comparison_jobs(limit=1)
        self.assertEqual(domain_payload(self.client.get(f'/api/v1/student/courses/{self.course.pk}/learning/'))['comparison_status'], 'ready')

    @override_settings(STUDENT_SHARED_RESULTS_COURSE_IDS=frozenset({'999999'}))
    def test_unlisted_course_preserves_legacy_result_path(self):
        self._prepare()
        from apps.analytics.teaching.student_projection import _load_bundle
        with patch('apps.analytics.teaching.student_projection._load_bundle', wraps=_load_bundle) as load:
            result = self.client.get(self._url())
        self.assertEqual(result.status_code, 200)
        self.assertNotIn('comparison_status', domain_payload(result))
        load.assert_called_once()
