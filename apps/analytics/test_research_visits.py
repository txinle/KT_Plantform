from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from django.test import SimpleTestCase

from .research_events import build_standard_behavior
from .research_visits import build_visits, match_visit, research_time
from .test_research_events import EVENT_HEADERS, event_row, state


class SinglePassVisits(list):
    def __init__(self, values):
        super().__init__(values)
        self.iterations = 0

    def __iter__(self):
        self.iterations += 1
        if self.iterations > 1:
            raise AssertionError("visits were scanned once per event")
        return super().__iter__()


class ResearchVisitTests(SimpleTestCase):
    def test_question_visit_options_exclude_other_questions(self):
        from .research_dataset import workspace_payload
        scope = SimpleNamespace(session_ref='S1', session_id=1, student_id=1, session_references=lambda: {'1': 'S1'})
        bundle = {'sessions': [{'session_ref': 'S1', 'student_id': 1, 'started_at': '1'}], 'students': [],
            'responses': [{'session_id': '1', 'question_ref': 'Q001'}, {'session_id': '1', 'question_ref': 'Q002'}],
            'visits': [{'session_id': '1', 'question_ref': 'Q001', 'key': 'v1'}, {'session_id': '1', 'question_ref': 'Q002', 'key': 'v2'}], 'behavior': []}
        result = workspace_payload(bundle, scope, {'view': 'question', 'question_ref': 'Q001'})
        self.assertEqual([item['key'] for item in result['visits']], ['v1'])

    def setUp(self):
        self.start = datetime(2026, 9, 9, 1, 0, 0, 123000, tzinfo=timezone.utc)

    def event(self, kind, seconds, *, visit=None, nav=None, source='server', client=None):
        return SimpleNamespace(event_id=f'{kind}-{seconds}', event_type=kind, session_id=1, attempt_id=2,
            payload={'visit_id': visit} if visit else {}, correlation_id=nav,
            server_committed_at=self.start + timedelta(seconds=seconds),
            estimated_occurred_at_utc=None, client_wall_time_utc=client, actor_source=source)

    def record(self, pk, sequence, clicked=None):
        return SimpleNamespace(pk=pk, session_id=1, attempt_id=2, visit_index=sequence,
            event_sequence=sequence, client_occurred_at=clicked, server_received_at=self.start + timedelta(seconds=sequence),
            attempt=SimpleNamespace(assignment=SimpleNamespace(snapshot_id=3)))

    def test_three_lists_preserve_missing_click_and_leave_positions(self):
        entries = [self.event('item_entered', 1, visit=10, nav='nav1'), self.event('item_entered', 5, visit=11, nav='nav2')]
        left = self.event('item_left', 30, visit=10, source='client', client=self.start + timedelta(seconds=3))
        visits = build_visits([self.record(10, 1), self.record(11, 2, self.start + timedelta(seconds=4))], entries + [left], {'3': 'Q001'})
        self.assertEqual([research_time(item['clicked_at']) for item in visits], ['', '20260909T090004.123'])
        self.assertEqual([research_time(item['left_at']) for item in visits], ['20260909T090003.123', ''])
        self.assertEqual(visits[0]['duration_seconds'], 2)
        self.assertIsNone(visits[1]['duration_seconds'])

    def test_delayed_client_event_is_not_assigned_by_upload_time(self):
        entries = [self.event('item_entered', 1, visit=10), self.event('item_entered', 5, visit=11)]
        visits = build_visits([self.record(10, 1), self.record(11, 2)], entries, {'3': 'Q001'})
        late = self.event('window_blurred', 30, source='client', client=self.start + timedelta(seconds=2))
        self.assertIsNone(match_visit(late, visits))
        late.estimated_occurred_at_utc = late.client_wall_time_utc
        self.assertEqual(match_visit(late, visits)['key'], '10')
        unknown = self.event('window_blurred', 30, source='client')
        self.assertIsNone(match_visit(unknown, visits))

    def test_correlation_survives_clock_disagreement_without_fake_duration(self):
        entry = self.event('item_entered', 1, visit=10, nav='nav1')
        left = self.event('item_left', 10, nav='nav1', source='client', client=self.start - timedelta(seconds=1))
        visits = build_visits([self.record(10, 1)], [entry, left], {'3': 'Q001'})
        self.assertIsNone(visits[0]['duration_seconds'])
        self.assertIn('时钟不一致', visits[0]['problem'])

    def test_time_unknown_and_naive_are_blank(self):
        self.assertEqual(research_time(None), '')
        self.assertEqual(research_time('2026-09-09T01:00:00'), '')
        self.assertEqual(research_time(self.start), '20260909T090000.123')


class StandardBehaviorTests(SimpleTestCase):
    def test_terminal_unanswered_preserves_visit_draft_and_skip_facts(self):
        from .research_dataset import response_status
        self.assertEqual(response_status({'status': 'unanswered'}, submitted=False, visited=False, skipped=False), '未访问')
        self.assertEqual(response_status({'status': 'unanswered'}, submitted=False, visited=True, skipped=False), '已访问未作答')
        self.assertEqual(response_status({'status': 'unanswered'}, submitted=False, visited=True, skipped=True), '已跳过')
        self.assertEqual(response_status({'status': 'unanswered', 'draft_answer': '"文字"'}, submitted=False, visited=True, skipped=False), '已有答案未提交')

    def test_skip_is_projected_from_the_actual_leave_event(self):
        tables = {'assessment_events': (EVENT_HEADERS, [event_row('skip', 'item_left', 1, {'leave_reason': 'skip'})])}
        result = build_standard_behavior({'rows': tables, 'events': []}, question_refs={'31': 'Q001'},
            session_refs={'11': 'S20260909_0011'}, identities={'11': ('0001', '同名')}, visits=[])
        self.assertEqual((result[0]['category'], result[0]['action']), ('题目访问', '跳过题目'))

    def test_all_question_events_deduplicate_only_underlying_ids_and_keep_sequence(self):
        events = []
        previous = None
        for sequence, answer in enumerate(['A', 'B', 'A', None, 'A'], 1):
            events.append(event_row(str(sequence), 'response_transition', sequence,
                {'before_state': state('single_choice', previous), 'after_state': state('single_choice', answer),
                 'option_action': 'clear' if answer is None else 'select'}))
            previous = answer
        events.append(event_row('offline', 'network_offline', 6, {}))
        events.append(event_row('simulation', 'simulation_run_completed', 7, {'result': 0}))
        events.append(event_row('session-only', 'session_completed', 8, {}, attempt_id='', question_snapshot_id=''))
        tables = {'assessment_events': (EVENT_HEADERS, events), 'response_events': (EVENT_HEADERS, events[:2]),
            'attempts': (['attempt_id', 'session_id', 'question_snapshot_id'], [['21', '11', '31']])}
        result = build_standard_behavior({'rows': tables, 'events': []}, question_refs={'31': 'Q001'},
            session_refs={'11': 'S20260909_0011'}, identities={'11': ('0001', '同名')}, visits=[])
        self.assertEqual(len(result), 7)
        self.assertEqual([row['sequence'] for row in result], list(range(1, 8)))
        self.assertEqual([row['after'] for row in result[:5]], ['A', 'B', 'A', '', 'A'])
        self.assertEqual([row['action'] for row in result[-2:]], ['网络断开', '仿真完成'])
        self.assertTrue(all(row['occurred_at'] == '' for row in result))
        self.assertTrue(all(row['visit_index'] is None for row in result))

    def test_multiple_events_share_one_visit_index_for_all_matching_modes(self):
        start = datetime(2026, 9, 9, 1, 0, 0, tzinfo=timezone.utc)
        sources = [
            SimpleNamespace(
                event_id="explicit",
                event_type="window_blurred",
                session_id=11,
                attempt_id=21,
                payload={"visit_id": "10"},
                correlation_id=None,
                server_committed_at=start + timedelta(seconds=2),
                estimated_occurred_at_utc=None,
                client_wall_time_utc=None,
                actor_source="server",
            ),
            SimpleNamespace(
                event_id="correlated",
                event_type="window_focused",
                session_id=11,
                attempt_id=21,
                payload={},
                correlation_id="nav-11",
                server_committed_at=start + timedelta(seconds=5),
                estimated_occurred_at_utc=None,
                client_wall_time_utc=None,
                actor_source="server",
            ),
            SimpleNamespace(
                event_id="timestamp",
                event_type="document_hidden",
                session_id=11,
                attempt_id=21,
                payload={},
                correlation_id=None,
                server_committed_at=start + timedelta(seconds=3),
                estimated_occurred_at_utc=None,
                client_wall_time_utc=None,
                actor_source="server",
            ),
        ]
        rows = [
            event_row(source.event_id, source.event_type, sequence, {})
            for sequence, source in enumerate(sources, 1)
        ]
        visits = SinglePassVisits(
            [
                {
                    "key": "10",
                    "session_id": "11",
                    "attempt_id": "21",
                    "question_ref": "Q001",
                    "visit_index": 1,
                    "entered_at": (start + timedelta(seconds=1)).isoformat(),
                    "left_at": (start + timedelta(seconds=4)).isoformat(),
                    "navigation_id": "nav-10",
                    "entry_event_id": "entry-10",
                },
                {
                    "key": "11",
                    "session_id": "11",
                    "attempt_id": "21",
                    "question_ref": "Q001",
                    "visit_index": 2,
                    "entered_at": (start + timedelta(seconds=4)).isoformat(),
                    "left_at": "",
                    "navigation_id": "nav-11",
                    "entry_event_id": "entry-11",
                },
            ]
        )
        tables = {
            "assessment_events": (EVENT_HEADERS, rows),
            "attempts": (
                ["attempt_id", "session_id", "question_snapshot_id"],
                [["21", "11", "31"]],
            ),
        }

        result = build_standard_behavior(
            {"rows": tables, "events": sources},
            question_refs={"31": "Q001"},
            session_refs={"11": "S20260909_0011"},
            identities={"11": ("0001", "同名")},
            visits=visits,
        )

        self.assertEqual(visits.iterations, 1)
        self.assertEqual([item["visit_key"] for item in result], ["10", "11", "10"])
