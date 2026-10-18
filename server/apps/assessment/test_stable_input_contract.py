"""Stable text capture is a separately bound, immutable measurement contract."""
import uuid
from types import SimpleNamespace

from django.test import TestCase, override_settings

from . import recording_capture_services as contracts
from .models import ExperimentSession
from .test_prepared_remediation import PreparedFixtureMixin


@override_settings(CAPTURE_STABLE_INPUT_ENABLED=True)
class StableInputContractTests(TestCase):
    def test_new_contract_preserves_original_v2_hash(self):
        original = contracts.ensure_capture_contract_v2()
        digest = original.schema_sha256
        self.assertEqual(digest, '96c7a43db1556a4f7ff5813f349993619e91c360731b3205ca4583bb487c3c82')
        stable = contracts.ensure_capture_contract_v2_stable_input()
        original.refresh_from_db()
        self.assertEqual(original.schema_sha256, digest)
        self.assertNotEqual(stable.pk, original.pk)
        self.assertEqual(stable.contract_name, 'capture-v2-stable-input-v1')
        self.assertEqual(original.schema_document['parameters']['stable_state_idle_ms'], 0)
        for name, value in {'text_state_policy': 'stable_input', 'stable_state_idle_ms': 1000,
                            'text_input_operations': 'burst_summary',
                            'continuous_input_periodic_snapshot': False}.items():
            self.assertEqual(stable.schema_document['parameters'][name], value)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}))
    def test_selector_shares_stable_contract_and_retains_ordinary_rollout(self):
        student, experiment = SimpleNamespace(pk=1), SimpleNamespace(course_id=2)
        ordinary = contracts.capture_contract_for_start(student, experiment)
        self.assertEqual(ordinary.contract_name, 'capture-v2-stable-input-v1')
        with override_settings(CAPTURE_V2_ENABLED=False):
            self.assertEqual(contracts.capture_contract_for_start(student, experiment).contract_name, 'capture-v1')
            prepared = contracts.capture_contract_for_start(student, experiment, require_capture_v2=True)
        self.assertEqual(prepared.pk, ordinary.pk)

    @override_settings(CAPTURE_V2_ENABLED=True, CAPTURE_V2_COURSE_IDS=frozenset({'*'}),
                       CAPTURE_STABLE_INPUT_ENABLED=False)
    def test_allocation_switch_reverts_only_new_v2_artifacts(self):
        student, experiment = SimpleNamespace(pk=1), SimpleNamespace(course_id=2)
        for prepared in (True, False):
            self.assertEqual(contracts.capture_contract_for_start(student, experiment,
                require_capture_v2=prepared).contract_name, 'capture-v2')
        stable = contracts.ensure_capture_contract_v2_stable_input()
        existing = SimpleNamespace(capture_contract_id=stable.pk, capture_contract=stable)
        self.assertEqual(contracts.text_capture_policy(existing), {'id': 'stable-input-v1', 'idle_ms': 1000})
        with override_settings(CAPTURE_V2_ENABLED=False):
            self.assertEqual(contracts.capture_contract_for_start(student, experiment).contract_name, 'capture-v1')


@override_settings(ASSESSMENT_PREPARED_ENABLED=True, CAPTURE_STABLE_INPUT_ENABLED=True)
class StablePreparedContractTests(PreparedFixtureMixin, TestCase):
    def test_disabling_new_allocation_preserves_bound_stable_prepared_session(self):
        from .prepared_services import activate_prepared_session
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        contract_id = session.capture_contract_id
        with override_settings(CAPTURE_STABLE_INPUT_ENABLED=False):
            session, receipt = activate_prepared_session(student=self.student, task_id=self.task.pk,
                client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        self.assertEqual(session.capture_contract_id, contract_id)
        self.assertEqual(receipt['runtime']['process']['text_capture_policy'],
                         {'id': 'stable-input-v1', 'idle_ms': 1000})

    def test_coverage_distinguishes_new_contract_from_original_v2(self):
        from apps.analytics.recording_coverage import generate_session_coverage
        from .prepared_services import activate_prepared_session
        session, _ = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        stable_run = generate_session_coverage(session)
        self.assertEqual(stable_run.coverage_version, 'coverage-v4')
        self.assertEqual(stable_run.capture_contract_hashes, [session.capture_contract.schema_sha256])
        self.assertFalse(stable_run.parameter_document['stable_input_keystroke_timing_available'])
        original = contracts.ensure_capture_contract_v2()
        ExperimentSession.objects.filter(pk=session.pk).update(capture_contract=original)
        session.refresh_from_db()
        old_run = generate_session_coverage(session)
        self.assertEqual(old_run.coverage_version, 'coverage-v3')
        self.assertEqual(old_run.capture_contract_hashes, [original.schema_sha256])
        self.assertNotEqual(old_run.source_hash, stable_run.source_hash)

    def test_repair_projection_distinguishes_queue_pressure_and_retry(self):
        from unittest.mock import patch
        from .prepared_services import preparation_repair_status
        for pressure, persisted, attempts, expected in (
            ('normal', 'queued', 0, 'queued'), ('capture_priority', 'queued', 0, 'paused'),
            ('normal', 'failed', 1, 'retrying'), ('capture_emergency', 'failed', 3, 'exhausted')):
            with self.subTest(expected=expected), patch('apps.analytics.runtime_pressure.runtime_pressure',
                    return_value={'mode': pressure}):
                self.assertEqual(preparation_repair_status(SimpleNamespace(status=persisted, attempts=attempts))['status'], expected)

    def test_prepared_runtime_projects_bound_contract_and_repair_never_rebinds(self):
        from .prepared_services import activate_prepared_session, prepare_participant, prepared_runtime
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        self.assertEqual(session.capture_contract.contract_name, 'capture-v2-stable-input-v1')
        self.assertEqual(session.prepared_artifact.first_runtime['process']['text_capture_policy'],
                         {'id': 'stable-input-v1', 'idle_ms': 1000})
        # Even an unopened old prepared session stays on its original contract.
        original = contracts.ensure_capture_contract_v2()
        ExperimentSession.objects.filter(pk=session.pk).update(capture_contract=original)
        session.prepared_artifact.__class__.objects.filter(session=session).update(ready=False)
        prepare_participant(session.participant_snapshot)
        session.refresh_from_db()
        self.assertEqual(session.capture_contract_id, original.pk)
        session, receipt = activate_prepared_session(student=self.student, task_id=self.task.pk,
            client_instance_id=uuid.uuid4(), navigation_id=uuid.uuid4())
        self.assertEqual(receipt['runtime']['process']['text_capture_policy'],
                         {'id': 'confirmed-transition-v1', 'idle_ms': 0})
        attempt = session.attempts.select_related('assignment').get()
        attempt.session = session
        self.assertEqual(prepared_runtime(attempt)['process']['text_capture_policy'],
                         {'id': 'confirmed-transition-v1', 'idle_ms': 0})

    def test_exhausted_preparation_is_reported_without_reset_or_start(self):
        from .prepared_models import PreparedSessionArtifact, PreparedSessionRepair
        from .prepared_services import enqueue_preparation_repair
        session = ExperimentSession.objects.get(class_task=self.task, student=self.student)
        PreparedSessionArtifact.objects.filter(session=session).update(ready=False)
        job = enqueue_preparation_repair(session.participant_snapshot)
        PreparedSessionRepair.objects.filter(pk=job.pk).update(status='failed', attempts=3)
        self.client.force_login(self.student)
        for _ in range(2):
            response = self.client.post(f'/api/v1/student/tasks/{self.task.pk}/start/',
                {'client_instance_id': str(uuid.uuid4()), 'navigation_id': str(uuid.uuid4())},
                content_type='application/json')
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(response.json()['error']['fields']['preparation']['status'], 'exhausted')
        job.refresh_from_db()
        session.refresh_from_db()
        self.assertEqual((job.status, job.attempts), ('failed', 3))
        self.assertIsNone(session.started_at)
