from django.test import TestCase

from apps.experiments import test_group_snapshots
from .models import Attempt, ConfidenceAssignment, QuestionVisitEvent


class PresentationRuntimeTests(TestCase):
    setUp = test_group_snapshots.GroupSnapshotTests.setUp
    publish = test_group_snapshots.GroupSnapshotTests.publish
    start_session = test_group_snapshots.GroupSnapshotTests.start_session

    def test_basic_question_kind_matches_prepared_and_active_navigation(self):
        from .group_runtime import presentation_runtime_projection
        from .prepared_services import _presentation_units
        experiment, _ = self.publish()
        session = self.start_session(experiment)
        attempts = list(session.attempts.select_related('assignment__snapshot__group_snapshot').order_by('assignment__assigned_sequence_index'))
        prepared = _presentation_units(attempts)
        active = presentation_runtime_projection(session, attempts[0])['presentation_units']
        for units in [prepared, active]:
            self.assertEqual([unit['kind'] for unit in units], ['reading', 'fill_blank'])

    def test_whole_question_state_and_confidence_are_authoritative_without_entry(self):
        from .group_runtime import presentation_runtime_projection
        experiment, _ = self.publish(confidence_mode='all')
        session = self.start_session(experiment)
        self.assertEqual(self.entry_runtime['presentation_progress']['total'], 2)
        self.assertEqual(len(self.entry_runtime['group_member_state']), 3)
        members = list(session.attempts.filter(assignment__snapshot__group_snapshot__isnull=False).order_by('assignment__snapshot__group_order'))
        Attempt.objects.filter(pk__in=[member.pk for member in members[:2]]).update(draft_answer='A')
        before = QuestionVisitEvent.objects.filter(session=session).count()
        result = presentation_runtime_projection(session, members[0])
        self.assertEqual(len(result['presentation_units']), 2)
        group = result['presentation_units'][0]
        self.assertEqual(group['answered_count'], 2)
        self.assertEqual(group['member_count'], 3)
        self.assertEqual(group['status'], 'partial')
        self.assertEqual(group['maximum_score'], '6.00')
        self.assertEqual(result['presentation_progress'], {'total': 2, 'answered': 0, 'maximum_score': '9.50'})
        state = result['group_member_state'][str(members[1].assignment_id)]
        self.assertTrue(state['response_write_allowed'])
        self.assertIsNotNone(state['pending_confidence'])
        self.assertEqual(QuestionVisitEvent.objects.filter(session=session).count(), before)
        ConfidenceAssignment.objects.filter(attempt=members[1]).update(condition=ConfidenceAssignment.Condition.NOT_ASSIGNED,
            response_status=ConfidenceAssignment.ResponseStatus.NOT_ASSIGNED)
        result = presentation_runtime_projection(session, members[0])
        self.assertIsNone(result['group_member_state'][str(members[1].assignment_id)]['pending_confidence'])
        self.assertNotIn('correct_answer', str(result))
        self.assertNotIn('explanation', str(result))
        self.client.force_login(self.student)
        response = self.client.get(f'/api/v1/student/sessions/{session.pk}/summary/')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['data']['presentation_question_count'], 2)
        self.assertEqual(response.json()['data']['attempt_count'], 4)
        self.assertIn('feedback_phase', response.json()['data']['result'])

    def test_result_content_retains_the_frozen_shared_word_bank(self):
        from .student_results import result_groups
        experiment, _ = self.publish()
        session = self.start_session(experiment)
        attempts = list(session.attempts.select_related('assignment__snapshot__group_snapshot'))
        frozen = attempts[0].assignment.snapshot.group_snapshot
        frozen.kind = 'words'
        frozen.material = 'A {{b01}} near the {{b02}} and {{b03}}.'
        frozen.word_bank = ['garden', 'school']
        for attempt in attempts:
            if attempt.assignment.snapshot.group_snapshot_id:
                attempt.assignment.snapshot.group_snapshot = frozen
                attempt.assignment.snapshot.question_type = 'fill_blank'
                attempt.assignment.snapshot.blank_id = f'b{attempt.assignment.snapshot.group_order:02}'
        groups = result_groups(attempts, [], {}, {}, show_scores=False)
        self.assertEqual(groups[0]['word_bank'], ['garden', 'school'])
        self.assertNotIn('correct_answer', str(groups))
