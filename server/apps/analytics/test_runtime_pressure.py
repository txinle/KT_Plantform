import json
import os
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase
from django.utils import timezone


def state(mode='normal', retry=5):
    return {'mode': mode, 'expires_at': time.time()+30, 'retry_after_seconds': retry}


class RuntimePressureReaderTests(SimpleTestCase):
    def test_missing_optional_path_does_not_read_files(self):
        from .runtime_pressure import runtime_pressure
        with patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': ''}), patch.object(Path, 'open', side_effect=AssertionError('unexpected file read')):
            self.assertEqual(runtime_pressure()['mode'], 'normal')

    def test_cached_file_read_is_bounded_and_returns_copy(self):
        from .runtime_pressure import runtime_pressure
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pressure.json'
            path.write_text(json.dumps({'mode': 'capture_priority', 'expires_at': 1030, 'retry_after_seconds': 10}))
            with patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': str(path)}), patch('apps.analytics.runtime_pressure.time.time', return_value=1000), patch('apps.analytics.runtime_pressure.time.monotonic', return_value=100) as clock:
                original = runtime_pressure()
                original['mode'] = 'normal'
                path.write_text(json.dumps({'mode': 'capture_emergency', 'expires_at': 1030, 'retry_after_seconds': 10}))
                self.assertEqual(runtime_pressure()['mode'], 'capture_priority')
                clock.return_value = 102
                self.assertEqual(runtime_pressure()['mode'], 'capture_emergency')

    def test_expiry_applies_even_before_cache_refresh(self):
        from .runtime_pressure import runtime_pressure
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pressure.json'
            path.write_text(json.dumps({'mode': 'capture_emergency', 'expires_at': 1001, 'retry_after_seconds': 30}))
            with patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': str(path)}), patch('apps.analytics.runtime_pressure.time.monotonic', return_value=100), patch('apps.analytics.runtime_pressure.time.time', return_value=1000) as clock:
                self.assertEqual(runtime_pressure()['mode'], 'capture_emergency')
                clock.return_value = 1002
                self.assertEqual(runtime_pressure()['mode'], 'normal')

    def test_missing_malformed_and_invalid_files_return_normal(self):
        from .runtime_pressure import runtime_pressure
        invalid = ['{', '[]', json.dumps({'mode': 'unexpected', 'expires_at': 1030, 'retry_after_seconds': 5}),
                   json.dumps({'mode': 'capture_priority', 'expires_at': float('nan'), 'retry_after_seconds': 5}),
                   json.dumps({'mode': 'capture_priority', 'expires_at': 1030, 'retry_after_seconds': 31}),
                   '['*2048+']'*2048]
        with tempfile.TemporaryDirectory() as directory, patch('apps.analytics.runtime_pressure.time.time', return_value=1000):
            for index, content in enumerate(invalid):
                path = Path(directory)/f'pressure-{index}.json'
                path.write_text(content)
                with self.subTest(content=content), patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': str(path)}):
                    self.assertEqual(runtime_pressure()['mode'], 'normal')
            with patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': str(Path(directory)/'missing')}):
                self.assertEqual(runtime_pressure()['mode'], 'normal')

    def test_parser_depth_failure_returns_normal(self):
        from .runtime_pressure import runtime_pressure
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'pressure.json'
            path.write_text('{}')
            with patch.dict(os.environ, {'SCHOOL_RUNTIME_PRESSURE_FILE': str(path)}), patch('apps.analytics.runtime_pressure.json.loads', side_effect=RecursionError):
                self.assertEqual(runtime_pressure()['mode'], 'normal')


class RuntimePressureClaimTests(TestCase):
    def setUp(self):
        from .test_teacher_data_center_api import TeacherDataCenterApiTests
        TeacherDataCenterApiTests.setUp(self)

    def test_research_priority_and_emergency_preserve_history_until_normal(self):
        from .models import ResearchSessionMaterialization
        from .research_materialization import PROJECTION_VERSION, claim_session_materialization
        from .test_research_materialization_jobs import ResearchMaterializationJobTests
        for index, mode in enumerate(('capture_priority', 'capture_emergency')):
            with self.subTest(mode=mode):
                fresh_session = ResearchMaterializationJobTests._another_session(self, index*2+1)
                old_session = ResearchMaterializationJobTests._another_session(self, index*2+2)
                fresh = ResearchSessionMaterialization.objects.create(session=fresh_session, priority=0, projection_version=PROJECTION_VERSION)
                history = ResearchSessionMaterialization.objects.create(session=old_session, priority=1, projection_version=PROJECTION_VERSION)
                with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state(mode)):
                    self.assertEqual(claim_session_materialization(worker_id='worker').pk, fresh.pk)
                    self.assertIsNone(claim_session_materialization(worker_id='worker'))
                history.refresh_from_db()
                self.assertEqual(history.status, 'queued')
                self.assertEqual(history.retry_count, 0)
                restore_mode = 'normal' if index == 0 else 'recovering'
                with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state(restore_mode)):
                    self.assertEqual(claim_session_materialization(worker_id='worker').pk, history.pk)

    def test_pressure_does_not_rewrite_an_expired_history_lease(self):
        from datetime import timedelta
        from .models import ResearchSessionMaterialization
        from .research_materialization import PROJECTION_VERSION, claim_session_materialization
        lease = timezone.now()-timedelta(seconds=1)
        item = ResearchSessionMaterialization.objects.create(
            session=self.session, priority=1, projection_version=PROJECTION_VERSION,
            status='running', locked_by='previous-worker', lease_expires_at=lease,
        )
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('capture_priority')):
            self.assertIsNone(claim_session_materialization(worker_id='next-worker'))
        item.refresh_from_db()
        self.assertEqual((item.status, item.locked_by, item.retry_count, item.lease_expires_at),
                         ('running', 'previous-worker', 0, lease))

    def test_export_pressure_blocks_before_queries_without_rewriting_jobs(self):
        from .models import ExportJob, ExportSnapshot
        from .services import claim_export_job
        snapshot = ExportSnapshot.objects.create(course=self.course, created_by=self.teacher)
        job = ExportJob.objects.create(course=self.course, snapshot=snapshot, created_by=self.teacher)
        for mode in ('capture_priority', 'capture_emergency'):
            with self.subTest(mode=mode), patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state(mode)), self.assertNumQueries(0):
                self.assertIsNone(claim_export_job(worker_id='worker'))
        job.refresh_from_db()
        self.assertEqual(job.status, 'queued')
        self.assertEqual(job.retry_count, 0)
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state()):
            self.assertEqual(claim_export_job(worker_id='worker').pk, job.pk)


class RuntimePressureCommandTests(SimpleTestCase):
    def test_comparison_emergency_does_not_start_a_unit(self):
        from .management.commands.run_student_comparisons import Command
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('capture_emergency')), patch('apps.analytics.management.commands.run_student_comparisons.run_comparison_jobs') as run:
            Command().handle(once=True, limit=50, concurrency=1, poll_seconds=2)
        run.assert_not_called()

    def test_comparison_always_yields_after_one_unit(self):
        from .management.commands.run_student_comparisons import Command
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('capture_priority')), patch('apps.analytics.management.commands.run_student_comparisons.run_comparison_jobs', return_value={'completed': 1, 'failed': 0}) as run:
            Command().handle(once=True, limit=50, concurrency=1, poll_seconds=2)
        run.assert_called_once_with(limit=1)

    def test_comparison_checks_emergency_between_completed_units(self):
        from .management.commands.run_student_comparisons import Command
        with patch('apps.analytics.runtime_pressure.runtime_pressure', side_effect=[state(), state('capture_emergency')]), patch('apps.analytics.management.commands.run_student_comparisons.run_comparison_jobs', return_value={'completed': 1, 'failed': 0}) as run, patch('apps.analytics.management.commands.run_student_comparisons.time.sleep', side_effect=[None, StopIteration]):
            with self.assertRaises(StopIteration):
                Command().handle(once=False, limit=50, concurrency=1, poll_seconds=2)
        run.assert_called_once_with(limit=1)

    def test_research_already_claimed_unit_finishes_after_pressure_changes(self):
        from .management.commands.run_research_materializations import Command
        pressure = state()
        def finish(*args, **kwargs):
            pressure.update(state('capture_emergency'))
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=pressure), patch('apps.analytics.management.commands.run_research_materializations.claim_session_materialization', return_value=SimpleNamespace(pk=1)), patch('apps.analytics.management.commands.run_research_materializations.run_session_materialization', side_effect=finish) as run, patch('apps.analytics.management.commands.run_research_materializations.threading.Thread'), patch('apps.analytics.management.commands.run_research_materializations.materialization_metrics', return_value={}):
            self.assertTrue(Command().run_once(worker_id='worker'))
        run.assert_called_once()

    def test_recovering_research_yields_only_after_completed_unit(self):
        from .management.commands.run_research_materializations import Command
        events = []
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('recovering', 10)), patch('apps.analytics.management.commands.run_research_materializations.claim_session_materialization', return_value=SimpleNamespace(pk=1)), patch('apps.analytics.management.commands.run_research_materializations.run_session_materialization', side_effect=lambda *args, **kwargs: events.append('finished')), patch('apps.analytics.management.commands.run_research_materializations.threading.Thread'), patch('apps.analytics.management.commands.run_research_materializations.materialization_metrics', return_value={}), patch('apps.analytics.management.commands.run_research_materializations.time.sleep', side_effect=lambda delay: events.append(('yield', delay))):
            self.assertTrue(Command().run_once(worker_id='worker'))
        self.assertEqual(events, ['finished', ('yield', 10)])

    def test_recovering_export_yields_after_completed_job(self):
        from .management.commands.run_export_jobs import Command
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('recovering', 10)), patch('apps.analytics.research_deletion.retry_artifact_cleanups'), patch('apps.analytics.management.commands.run_export_jobs.claim_export_job', side_effect=[SimpleNamespace(), None]), patch('apps.analytics.management.commands.run_export_jobs.run_export_job') as run, patch('apps.analytics.management.commands.run_export_jobs.time.sleep') as sleep:
            Command().handle(once=False)
        run.assert_called_once()
        sleep.assert_called_once_with(10)

    def test_recovering_export_once_still_yields_after_completed_job(self):
        from .management.commands.run_export_jobs import Command
        with patch('apps.analytics.runtime_pressure.runtime_pressure', return_value=state('recovering', 10)), patch('apps.analytics.research_deletion.retry_artifact_cleanups'), patch('apps.analytics.management.commands.run_export_jobs.claim_export_job', return_value=SimpleNamespace()), patch('apps.analytics.management.commands.run_export_jobs.run_export_job') as run, patch('apps.analytics.management.commands.run_export_jobs.time.sleep') as sleep:
            Command().handle(once=True)
        run.assert_called_once()
        sleep.assert_called_once_with(10)
