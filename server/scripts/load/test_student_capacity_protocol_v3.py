"""Frozen v3 workload, split driver and capture queue contracts."""
import copy
import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent))
import test_student_capacity_protocol as v2_tests


def fixture():
    row = v2_tests.CapacityProtocolTests().fixture()
    row.update(source_fixture_id='fixture-1', baseline_snapshot_id='snapshot-1')
    row['source_ownership']['command'] = 'prepare_student_capacity_baseline'
    for account in row['accounts']:
        first = account['questions'][0]
        account['questions'] = [{**copy.deepcopy(first), 'assignment_id': account['user_id'] * 100 + i,
                                'attempt_id': account['user_id'] * 1000 + i,
                                'question_type': 'short_answer' if i % 2 else 'single_choice',
                                'confidence_condition': 'prompt' if i % 3 else 'none'} for i in range(20)]
    from student_concurrency import digest
    row['fixture_spec'] = {'version': 'student-capacity-baseline-v1', 'content_binding_sha256': digest(row['accounts'])}
    row['fixture_id'] = row['plan_sha'] = digest(row['fixture_spec'])
    return row


class V3Tests(unittest.TestCase):
    def subject(self):
        self.assertIsNotNone(importlib.util.find_spec('student_capacity_protocol_v3'), 'v3 implementation is missing')
        import student_capacity_protocol_v3
        return student_capacity_protocol_v3

    def plan(self, **kwargs):
        return self.subject().build_plan(fixture(), mode=kwargs.pop('mode', 'save-only'), count=kwargs.pop('count', 50),
            run_id='cell-1', fixture_sha256='a' * 64, base_url='http://127.0.0.1:18085',
            workload=kwargs.pop('workload', 'comparison-2q'), workload_profile='historical-v3', **kwargs)

    def test_full_denominator_and_disjoint_driver_split(self):
        plan = self.plan(browser_account_ids=['a0', 'a1'])
        self.assertEqual(plan['version'], 'student-capacity-protocol-v3')
        self.assertEqual(plan['count'], 50)
        self.assertEqual(sum(r['driver'] == 'protocol' for r in plan['roster']), 48)
        self.assertEqual(plan['source_fixture_id'], 'fixture-1')
        self.assertEqual(plan['baseline_snapshot_id'], 'snapshot-1')
        with self.assertRaises(ValueError):
            self.plan(browser_account_ids=['a0', 'a0'])

    def test_changed_account_material_cannot_reuse_the_baseline_binding(self):
        subject = self.subject()
        changed = fixture()
        changed['accounts'][-1]['questions'][-1]['question_type'] = 'numeric'
        with self.assertRaisesRegex(ValueError, 'binding'):
            subject.build_plan(changed, mode='login', count=50, run_id='cell', fixture_sha256='a' * 64,
                               base_url='http://127.0.0.1:18085', workload_profile='historical-v3')

    def test_entry_mixed_has_three_fixed_roles(self):
        from collections import Counter
        self.assertEqual(Counter(r['role'] for r in self.plan(mode='entry-mixed')['roster']),
                         {'login': 17, 'startup': 17, 'save': 16})
        self.assertEqual(Counter(r['role'] for r in self.plan(mode='entry-mixed', count=180)['roster']),
                         {'login': 60, 'startup': 60, 'save': 60})

    def test_paper_covers_twenty_items_and_ten_minutes_with_clear_and_return(self):
        plan = self.plan(workload='paper-20q')
        row = plan['roster'][0]
        self.assertEqual(len(row['answers']), 20)
        self.assertEqual(plan['workload_duration_ms'], 600000)
        self.assertEqual({a['assignment_id'] for a in row['save_schedule']}, {a['assignment_id'] for a in row['answers']})
        self.assertEqual([a['offset_ms'] for a in row['save_schedule'] if a['assignment_id'] == row['answers'][0]['assignment_id']],
                         list(range(1000, 30000, 2000)))
        text = [a for a in row['save_schedule'] if a['assignment_id'] == row['answers'][1]['assignment_id']]
        self.assertIn('', [a['value'] for a in text])
        self.assertEqual(text[0]['value'], text[3]['value'])
        self.assertEqual({a['confidence_probability'] for a in row['answers']}, {None, 0, 80})

    def test_comparison_preserves_fifteen_two_second_changes(self):
        plan = self.plan()
        self.assertEqual(len(plan['roster'][0]['save_schedule']), 15)
        self.assertEqual([a['offset_ms'] for a in plan['roster'][0]['save_schedule']], list(range(0, 30000, 2000)))

    def test_pointer_queue_coalesces_but_keeps_all_raw_actions(self):
        subject = self.subject()
        queue = subject.LatestPointerQueue()
        queue.put({'event_id': 'A'}, now=0)
        queue.put({'event_id': 'B'}, now=.03)
        queue.put({'event_id': 'A2'}, now=.06)
        self.assertIsNone(queue.take(now=.1))
        self.assertEqual(queue.take(now=.16), {'event_id': 'A2'})
        queue.put({'event_id': 'C'}, now=.17)
        self.assertIsNone(queue.take(now=1))  # one in flight
        queue.complete()
        self.assertEqual(queue.take(now=1), {'event_id': 'C'})

    def test_queue_maximum_wait_and_new_event_with_equal_content(self):
        subject = self.subject()
        queue = subject.LatestPointerQueue()
        for index in range(6):
            queue.put({'event_id': str(index), 'answer': 'A'}, now=index * .04)
        self.assertEqual(queue.take(now=.25)['event_id'], '5')
        queue.complete()
        queue.put({'event_id': 'same-content-new-head', 'answer': 'A'}, now=.26)
        self.assertEqual(queue.take(now=.36)['event_id'], 'same-content-new-head')

    def test_login_uses_current_single_courses_tasks_chain_and_nullable_error(self):
        subject = self.subject()
        plan = self.plan(mode='login')
        class Client:
            csrf = 'fresh'
            calls = []
            def request(client, path, document=None, **kwargs):
                client.calls.append(path)
                if path.endswith('/tasks/'):
                    return {'results': [{'id': fixture()['accounts'][0]['taskId']}]}, 200, None
                return {'error': None}, 200, None
        client = Client()
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, client)
        actor.login()
        self.assertEqual(client.calls, ['/api/v1/auth/csrf/', '/api/v1/auth/login/', '/api/v1/student/courses/',
                                      f'/api/v1/student/courses/{actor.account["courseId"]}/tasks/'])

    def test_burst_keeps_raw_transitions_and_replays_only_original_unknown_request(self):
        subject = self.subject()
        plan = self.plan(workload='paper-20q')
        planned = plan['roster'][0]
        question = planned['answers'][1]
        class Client:
            calls = []
            def request(client, path, document=None, **kwargs):
                client.calls.append(copy.deepcopy(document))
                if len(client.calls) == 1:
                    raise subject.ProtocolFailure('transport_error')
                return {'committed': True, 'active_response_event_id': document['target_event_id'], 'response_version': 1,
                    'inline_confidence': document['inline_confidence'],
                    'acknowledgements': [{'event_id': e['event_id'], 'client_sequence_no': e['client_sequence_no'],
                                         'server_ingest_sequence_no': e['client_sequence_no']} for e in document['capture_bundle']['events']],
                    'checkpoint_ids': [c['checkpoint_id'] for c in document['capture_bundle']['checkpoints']]}, 200, None
        actor = subject.Actor(fixture()['accounts'][0], planned, plan, Client())
        actor.dto = {'assignment': question, 'question': question, 'pending_confidence': {'confidence_assignment_id': 'id'},
                     'actions': {'save_draft_url': '/save'}}
        actor.lease = {'writer_lease_token': 'private', 'writer_client_instance_id': actor.client_id}
        burst = [(i, a) for i, a in enumerate(planned['save_schedule']) if a['assignment_id'] == question['assignment_id']][:4]
        for index, action in burst:
            actor._emit_action(index, action, 0)
        with patch.object(actor, 'ensure_lease'):
            pointer = actor.queue.take(now=10**10, force=True)
            actor._send_pointer(pointer)
        self.assertEqual(actor.client.calls[0], actor.client.calls[1])
        samples = [actor.record['save_samples'][i] for i, _ in burst]
        self.assertEqual([s['status'] for s in samples], ['superseded'] * 3 + ['acknowledged'])
        events = [e for e in actor.record['capture_events'] if e['event_type'] == 'response_transition']
        self.assertEqual(len(events), 4)
        self.assertEqual(len({e['event_id'] for e in events}), 4)
        self.assertEqual([e['payload']['after_state']['value'] for e in events], [a['value'] for _, a in burst])
        self.assertEqual(actor.expected[question['attempt_id']]['confidence_probability'], 0)

    def test_submission_clock_includes_final_queue_drain_before_close(self):
        subject = self.subject()
        plan = self.plan(workload='paper-20q')
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, object())
        moment = [5]
        actor.flush_pointer = lambda: moment.__setitem__(0, 7)
        def submitted(_actor, **kwargs):
            _actor.record.update(submission_ms=100, completion_ms=1100)
        with patch.object(subject.v2.Actor, 'submit', submitted):
            actor.submit(clock=lambda: moment[0])
        self.assertEqual(actor.record['submission_ms'], 2100)
        self.assertEqual(actor.record['completion_ms'], 3100)
        self.assertEqual(actor.record['processing_ms'], 1000)

    def test_submission_confirmation_clock_excludes_prior_history_review(self):
        subject = self.subject()
        plan = self.plan(workload='paper-20q')
        actor = subject.Actor(fixture()['accounts'][0], plan['roster'][0], plan, object())
        actor._submission_started_at = 1
        actor.flush_pointer = lambda: None
        def submitted(_actor, **kwargs):
            _actor.record.update(submission_ms=100, completion_ms=1100)
        with patch.object(subject.v2.Actor, 'submit', submitted):
            actor.submit(clock=lambda: 7)
        self.assertEqual(actor.record['submission_ms'], 100)
        self.assertEqual(actor.record['submission_started_monotonic_ms'], 7000)

    def test_summary_preserves_browser_missing_in_full_denominator_without_pooling(self):
        subject = self.subject()
        plan = self.plan(mode='login', browser_account_ids=['a0', 'a1'])
        records = [{**p, 'run_id': plan['run_id'], 'plan_id': plan['plan_id'], 'status': 'completed',
                    'login_ms': 1, 'release_skew_ms': 0} for p in plan['roster'] if p['driver'] == 'protocol']
        summary = subject.summarize(plan, records)
        self.assertEqual(summary['counts']['planned'], 50)
        self.assertEqual(summary['counts']['missing'], 2)
        self.assertEqual(summary['login']['planned'], 48)
        self.assertFalse(summary['thresholds_met'])

    def test_event_and_pointer_delays_are_separate_and_missing_events_fail(self):
        subject = self.subject()
        plan = self.plan()
        records = [{**p, 'run_id': plan['run_id'], 'plan_id': plan['plan_id'], 'status': 'completed',
                    'release_skew_ms': 0, 'navigation_samples': [{'duration_ms': 10}] * len(p['navigation_schedule']),
                    'save_samples': [{'status': 'superseded', 'planned_monotonic_ms': 1000,
                        'event_ack_monotonic_ms': 1200, 'ack_monotonic_ms': 6000,
                        'capture_http_send_monotonic_ms': 1100, 'http_send_monotonic_ms': 5500,
                        'scheduled_ms': 5000, 'duration_ms': 500} for _ in p['save_schedule']]}
                   for p in plan['roster']]
        summary = subject.summarize(plan, records)
        self.assertEqual(summary['save_scheduled']['p95_ms'], 200)
        self.assertEqual(summary['pointer_scheduled']['p95_ms'], 5000)
        self.assertEqual(summary['save_scheduled']['p50_ms'], 200)
        self.assertTrue(summary['thresholds_met'])
        records[0]['save_samples'][0]['event_ack_monotonic_ms'] = None
        summary = subject.summarize(plan, records)
        self.assertEqual(summary['save_scheduled']['missing'], 1)
        self.assertFalse(summary['thresholds_met'])

    def test_background_limit_is_measured_after_receipt_not_confirmation(self):
        subject = self.subject()
        plan = self.plan(mode='submission')
        records = [{**p, 'run_id': plan['run_id'], 'plan_id': plan['plan_id'], 'status': 'completed',
                    'release_skew_ms': 0, 'submission_ms': 1000, 'processing_ms': 299500,
                    'completion_ms': 300500} for p in plan['roster']]
        # The percentile still fails at 60s, independently of the maximum rule.
        records[0]['processing_ms'] = 299500
        for record in records[1:]:
            record.update(processing_ms=100, completion_ms=1100)
        summary = subject.summarize(plan, records)
        self.assertTrue(summary['thresholds_met'])
        self.assertEqual(summary['completion']['max_ms'], 300500)


if __name__ == '__main__':
    unittest.main()
