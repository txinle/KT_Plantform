from datetime import UTC, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.assessment.response_snapshot_services import ResponseState

from .recording_coverage import _attempt_result, _derived_features, _response_chain


class CaptureV2BranchCoverageTests(SimpleTestCase):
    def event(
        self, key, parent, before, after, *, sequence=1, client='client-a',
        monotonic_ms=None, origin='origin-a', event_type='response_transition',
    ):
        payload = {}
        if event_type == 'response_transition':
            payload = {
                'parent_event_id': parent,
                'branch_id': key,
                'before_state': ResponseState('short_answer', before).state_json,
                'after_state': ResponseState('short_answer', after).state_json,
                'before_state_hash': ResponseState('short_answer', before).state_hash,
                'after_state_hash': ResponseState('short_answer', after).state_hash,
            }
        return SimpleNamespace(
            event_id=key,
            event_type=event_type,
            payload=payload,
            server_ingest_sequence_no=sequence,
            client_instance_id=client,
            client_time_origin=origin,
            client_monotonic_ms=(
                monotonic_ms if monotonic_ms is not None else sequence * 100
            ),
            client_wall_time_utc=None,
            estimated_occurred_at_utc=None,
            server_received_at=None,
        )

    def test_concurrent_successors_do_not_require_global_linear_chain(self):
        events = [self.event('c', 'a', 'A', 'C'), self.event('a', None, '', 'A'), self.event('b', 'a', 'A', 'B')]
        self.assertEqual(_response_chain(events), ('complete', []))

    def test_missing_parent_and_tampered_body_are_not_replayable(self):
        event = self.event('b', 'a', 'A', 'B')
        self.assertNotEqual(_response_chain([event])[0], 'complete')
        event.payload['parent_event_id'] = None
        event.payload['after_state']['value'] = 'tampered'
        self.assertNotEqual(_response_chain([event])[0], 'complete')

    def test_derived_features_follow_causal_path_and_count_return_transition(self):
        root = self.event('root', None, '', 'A', sequence=9, monotonic_ms=100)
        expanded = self.event(
            'expanded', 'root', 'A', 'AB', sequence=7, client='client-b',
            monotonic_ms=200, origin='origin-b',
        )
        returned = self.event(
            'returned', 'expanded', 'AB', 'A', sequence=1, client='client-b',
            monotonic_ms=300, origin='origin-b',
        )

        features = _derived_features([returned, root, expanded])

        self.assertEqual(features['revision_count'], 2)
        self.assertEqual(features['formal_path_event_ids'], ['root', 'expanded', 'returned'])
        self.assertEqual(features['revision_latency_ms'], None)
        self.assertEqual(features['state_holding_time_ms'], [None, 100])
        self.assertEqual(features['branch_metrics'], [{
            'branch_id': 'returned',
            'head_event_id': 'returned',
            'path_event_ids': ['root', 'expanded', 'returned'],
            'revision_count': 2,
            'first_response_latency_ms': None,
            'revision_latency_ms': None,
            'post_revision_time_ms': None,
            'state_holding_time_ms': [None, 100],
        }])

    def test_formal_boundary_freezes_selected_path_but_keeps_late_branch_metrics(self):
        rendered = self.event(
            'rendered', None, '', '', sequence=1, monotonic_ms=0,
            event_type='item_rendered',
        )
        root = self.event('root', None, '', 'A', sequence=2, monotonic_ms=100)
        formal = self.event('formal', 'root', 'A', 'AB', sequence=3, monotonic_ms=200)
        late_rendered = self.event(
            'late-rendered', None, '', '', sequence=4, monotonic_ms=90,
            event_type='item_rendered',
        )
        late = self.event('late', 'root', 'A', 'Z', sequence=5, monotonic_ms=300)
        attempt = SimpleNamespace(
            active_response_event_id='late',
            final_response_event_id='formal',
            formal_capture_boundary={
                'active_response_event_id': 'formal',
                'server_ingest_sequence_no': 3,
            },
        )

        frozen = _derived_features([rendered, root, formal], attempt=attempt)
        with_late_upload = _derived_features(
            [rendered, root, formal, late_rendered, late], attempt=attempt,
        )

        self.assertEqual(with_late_upload['formal_path_event_ids'], ['root', 'formal'])
        self.assertEqual(with_late_upload['revision_count'], 1)
        self.assertEqual(with_late_upload['first_response_latency_ms'], 100)
        self.assertEqual(with_late_upload['revision_latency_ms'], 100)
        self.assertEqual(
            with_late_upload['revision_count'], frozen['revision_count'],
        )
        self.assertEqual(
            with_late_upload['revision_latency_ms'], frozen['revision_latency_ms'],
        )
        self.assertEqual(
            with_late_upload['first_response_latency_ms'],
            frozen['first_response_latency_ms'],
        )
        self.assertEqual(
            with_late_upload['formal_path_event_ids'], frozen['formal_path_event_ids'],
        )
        self.assertEqual(
            [row['head_event_id'] for row in with_late_upload['branch_metrics']],
            ['formal', 'late'],
        )


class CaptureV2TextInputCoverageTests(SimpleTestCase):
    origin = datetime(2026, 9, 7, tzinfo=UTC)
    attempt_id = 'attempt-a'
    first_operation_id = '00000000-0000-0000-0000-000000000001'
    second_operation_id = '00000000-0000-0000-0000-000000000002'
    third_operation_id = '00000000-0000-0000-0000-000000000003'

    def event(
        self, key, event_type, *, sequence, question_type='short_answer',
        parent_event_id=None, before='', after='answer',
        operation_id=first_operation_id, composition='none', attempt_id=attempt_id,
    ):
        payload = {}
        if event_type == 'response_transition':
            before_state = ResponseState(question_type, before)
            after_state = ResponseState(question_type, after)
            payload = {
                'parent_event_id': parent_event_id,
                'branch_id': 'browser-branch',
                'operation_id': operation_id,
                'before_state': before_state.state_json,
                'after_state': after_state.state_json,
                'before_state_hash': before_state.state_hash,
                'after_state_hash': after_state.state_hash,
            }
        elif event_type == 'input_operation':
            payload = {
                'capture_version': 'capture-v2',
                'operation_id': operation_id,
                'input_type': 'insertText',
                'composition': composition,
            }
        return SimpleNamespace(
            event_id=key,
            event_type=event_type,
            payload=payload,
            attempt_id=attempt_id,
            server_ingest_sequence_no=sequence,
            client_instance_id='browser-a',
            client_time_origin=self.origin,
            client_monotonic_ms=sequence * 100,
            client_wall_time_utc=None,
            estimated_occurred_at_utc=None,
            server_received_at=None,
        )

    def coverage_result(
        self,
        question_type,
        *,
        with_input_operation,
        transition_operation_ids=None,
        input_operations=None,
        foreign_input_operation_ids=(),
        contract_name='capture-v2',
    ):
        transition_operation_ids = (
            [self.first_operation_id]
            if transition_operation_ids is None
            else transition_operation_ids
        )
        input_operations = (
            [(self.first_operation_id, 'none')]
            if input_operations is None and with_input_operation
            else (input_operations or [])
        )
        events = [
            self.event('entered', 'item_entered', sequence=1),
            self.event('rendered', 'item_rendered', sequence=2),
        ]
        for index, (operation_id, composition) in enumerate(
            input_operations, start=3,
        ):
            events.append(
                self.event(
                    f'operation-{index}',
                    'input_operation',
                    sequence=index,
                    operation_id=operation_id,
                    composition=composition,
                )
            )
        for index, operation_id in enumerate(
            foreign_input_operation_ids,
            start=3 + len(input_operations),
        ):
            events.append(
                self.event(
                    f'foreign-operation-{index}',
                    'input_operation',
                    sequence=index,
                    operation_id=operation_id,
                    attempt_id='attempt-b',
                )
            )
        prior_event_id = None
        prior_value = ''
        transition_start = 3 + len(input_operations) + len(foreign_input_operation_ids)
        for index, operation_id in enumerate(
            transition_operation_ids,
            start=transition_start,
        ):
            current_value = f'answer-{index}'
            events.append(
                self.event(
                    f'transition-{index}',
                    'response_transition',
                    sequence=index,
                    question_type=question_type,
                    parent_event_id=prior_event_id,
                    before=prior_value,
                    after=current_value,
                    operation_id=operation_id,
                )
            )
            prior_event_id = f'transition-{index}'
            prior_value = current_value
        events.extend([
            self.event(
                'left',
                'item_left',
                sequence=transition_start + len(transition_operation_ids),
            ),
        ])
        attempt = SimpleNamespace(
            assignment=SimpleNamespace(
                snapshot=SimpleNamespace(question_type=question_type),
            ),
            pk=self.attempt_id,
            final_response_snapshot_id='snapshot-1',
            active_response_event_id=prior_event_id,
            final_response_event_id=None,
            formal_capture_boundary={},
            session=SimpleNamespace(
                capture_contract=SimpleNamespace(contract_name=contract_name),
            ),
        )
        return _attempt_result(
            run=SimpleNamespace(pk='coverage-run'),
            attempt=attempt,
            events=events,
            session_reasons=[],
            gap_count=0,
            syncs=[],
        )

    def test_stable_input_accepts_one_burst_summary_per_stable_transition(self):
        result = self.coverage_result('short_answer', with_input_operation=True,
            contract_name='capture-v2-stable-input-v1')
        self.assertTrue(result.analysis_eligibility['text_editing']['usable'])
        self.assertEqual(result.overall_status, 'complete')

    def test_stable_input_still_requires_a_linked_operation_for_each_state(self):
        result = self.coverage_result('short_answer', with_input_operation=False,
            contract_name='capture-v2-stable-input-v1')
        self.assertIn('missing_input_operation_for_response_transition', result.missing_reasons)

    def test_capture_v2_input_operations_complete_text_editing_for_text_questions(self):
        for question_type in ('short_answer', 'fill_blank', 'numeric'):
            with self.subTest(question_type=question_type):
                result = self.coverage_result(
                    question_type, with_input_operation=True,
                )

                self.assertEqual(result.overall_status, 'complete')
                self.assertEqual(
                    result.analysis_eligibility['text_editing']['status'],
                    'complete',
                )
                self.assertTrue(result.analysis_eligibility['text_editing']['usable'])

    def test_capture_v2_missing_input_operation_marks_attempt_partial(self):
        result = self.coverage_result(
            'fill_blank', with_input_operation=False,
        )

        self.assertEqual(result.overall_status, 'partial')
        self.assertIn('missing_input_operation', result.missing_reasons)
        self.assertEqual(
            result.analysis_eligibility['text_editing']['status'], 'insufficient',
        )
        self.assertIn(
            'missing_input_operation',
            result.analysis_eligibility['text_editing']['missing_reasons'],
        )
        self.assertIn(
            'missing_input_operation_for_response_transition',
            result.analysis_eligibility['text_editing']['missing_reasons'],
        )

    def test_capture_v2_requires_each_text_transition_to_match_an_input_operation(self):
        result = self.coverage_result(
            'fill_blank',
            with_input_operation=True,
            input_operations=[(self.first_operation_id, 'none')],
            transition_operation_ids=[
                self.first_operation_id,
                self.second_operation_id,
                self.third_operation_id,
            ],
        )

        self.assertEqual(result.overall_status, 'partial')
        self.assertIn('missing_input_operation', result.missing_reasons)
        self.assertIn(
            'missing_input_operation_for_response_transition',
            result.missing_reasons,
        )
        self.assertEqual(
            result.analysis_eligibility['text_editing']['status'], 'insufficient',
        )

    def test_capture_v2_reused_operation_id_does_not_cover_multiple_transitions(self):
        result = self.coverage_result(
            'short_answer',
            with_input_operation=True,
            input_operations=[(self.first_operation_id, 'none')],
            transition_operation_ids=[
                self.first_operation_id,
                self.first_operation_id,
            ],
        )

        self.assertEqual(result.overall_status, 'partial')
        self.assertIn(
            'reused_input_operation_for_response_transition',
            result.missing_reasons,
        )
        self.assertEqual(
            result.analysis_eligibility['text_editing']['status'], 'insufficient',
        )

    def test_capture_v2_all_transition_operations_match_even_when_composition_repeats(self):
        result = self.coverage_result(
            'numeric',
            with_input_operation=True,
            input_operations=[
                (self.first_operation_id, 'started'),
                (self.first_operation_id, 'confirmed'),
                (self.second_operation_id, 'none'),
                (self.third_operation_id, 'none'),
            ],
            transition_operation_ids=[
                self.first_operation_id,
                self.second_operation_id,
                self.third_operation_id,
            ],
        )

        self.assertEqual(result.overall_status, 'complete')
        self.assertEqual(
            result.analysis_eligibility['text_editing']['status'], 'complete',
        )

    def test_capture_v2_does_not_match_another_attempts_input_operation(self):
        result = self.coverage_result(
            'short_answer',
            with_input_operation=False,
            transition_operation_ids=[self.first_operation_id],
            foreign_input_operation_ids=[self.first_operation_id],
        )

        self.assertEqual(result.overall_status, 'partial')
        self.assertIn('missing_input_operation', result.missing_reasons)

    def test_capture_v2_cancelled_composition_without_transition_is_not_missing(self):
        result = self.coverage_result(
            'short_answer',
            with_input_operation=True,
            input_operations=[(self.first_operation_id, 'cancelled')],
            transition_operation_ids=[],
        )

        self.assertNotIn('missing_input_operation', result.missing_reasons)
        self.assertEqual(
            result.analysis_eligibility['text_editing']['status'], 'complete',
        )
