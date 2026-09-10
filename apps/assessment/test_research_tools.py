import uuid
from django.core.exceptions import ValidationError
from django.test import TestCase
from . import test_capture_v2 as capture_fixtures
from .models import AssessmentEvent
from apps.question_bank.models import Question


class ResearchToolTests(TestCase):
    def setUp(self):
        config = {'check_enabled': True, 'doubt_enabled': True, 'rules': [{
            'id': 'check1', 'tool': 'check', 'trigger': 'item_enter', 'question_code': '',
            'after_seconds': 0, 'max_exposures': 1, 'required': True, 'hint_id': ''}]}
        self.version_configuration = {'research_tools': config}
        capture_fixtures.CaptureV2Tests.setUp(self)

    def enabled_attempt(self):
        return self.attempts[Question.Type.SINGLE]

    def test_marks_restore_and_rule_request_limit_is_server_enforced(self):
        from .research_tool_services import record_tool_action, available_tools, due_rules
        attempt = self.enabled_attempt()
        def action(tool, stage, **kwargs):
            return record_tool_action(student=self.student, attempt_id=attempt.pk, tool=tool,
                stage=stage, operation_id=str(uuid.uuid4()), **kwargs)
        action('doubt', 'mark_added')
        self.assertTrue(available_tools(attempt)['state']['doubt'])
        source = action('check', 'requested', data={'rule_id': 'check1'}, initiation_type='task_required')
        self.assertEqual(due_rules(attempt)[0]['pending_request']['event_id'], source['event_id'])
        with self.assertRaises(ValidationError):
            action('check', 'requested', data={'rule_id': 'check1'}, initiation_type='task_required')

    def test_retry_after_formal_submission_returns_original_tool_acknowledgement(self):
        from .research_tool_services import record_tool_action
        attempt = self.enabled_attempt()
        args = dict(student=self.student, attempt_id=attempt.pk, tool='doubt', stage='mark_added',
            operation_id=str(uuid.uuid4()))
        first = record_tool_action(**args)
        attempt.status = 'submitted'
        attempt.save(update_fields=['status'])
        self.assertEqual(record_tool_action(**args), first)
    def test_disabled_tools_do_not_create_usage(self):
        from .research_tool_services import record_tool_action
        attempt = self.attempts[Question.Type.SINGLE]
        with self.assertRaises(ValidationError):
            record_tool_action(student=self.student, attempt_id=attempt.pk, tool='hint',
                stage='requested', operation_id=str(uuid.uuid4()), data={'hint_id': 'absent'})
        self.assertFalse(AssessmentEvent.objects.filter(event_type='research_tool').exists())

    def test_configuration_rejects_steps_and_answer_hints_without_label(self):
        from apps.experiments.research_tool_config import validate_research_tools
        with self.assertRaises(ValidationError):
            validate_research_tools({'steps_enabled': True})
        with self.assertRaises(ValidationError):
            validate_research_tools({'hints': [{'id': 'h1', 'text': 'answer'}]})

    def test_confidence_tool_uses_the_frozen_v2_scale(self):
        from .research_tool_services import available_tools, record_tool_action

        attempt = self.enabled_attempt()
        configuration = self.version.configuration
        configuration.update({
            'confidence_mode': 'all',
            'confidence_scale_version': 'v2',
            'research_tools': {
                'rules': [{
                    'id': 'confidence1', 'tool': 'confidence', 'trigger': 'item_enter',
                    'question_code': '', 'after_seconds': 0, 'max_exposures': 1,
                    'required': True, 'hint_id': '',
                }],
            },
        })
        self.version.configuration = configuration
        self.version.save(update_fields=['configuration'])
        attempt.session.version = self.version

        self.assertEqual(available_tools(attempt)['confidence_scale']['step'], 1)
        base = {
            'student': self.student,
            'attempt_id': attempt.pk,
            'tool': 'confidence',
            'data': {'rule_id': 'confidence1'},
            'initiation_type': 'task_required',
        }
        record_tool_action(**base, stage='requested', operation_id=str(uuid.uuid4()))
        record_tool_action(**base, stage='presented', operation_id=str(uuid.uuid4()))
        record_tool_action(**{
            **base,
            'stage': 'responded',
            'operation_id': str(uuid.uuid4()),
            'data': {'rule_id': 'confidence1', 'probability': 57},
        })

        attempt.confidence_assignment.refresh_from_db()
        self.assertEqual(attempt.confidence_assignment.probability, 57)
