"""Old business URLs must not retain an alternative submission protocol."""
import json

from django.test import TestCase
from django.urls import reverse

from apps.accounts.models import User
from apps.question_bank.models import Question
from .models import AssessmentEvent, SubmissionEvent
from . import test_capture_v2 as fixtures


class RetiredSubmissionRouteTests(TestCase):
    def setUp(self):
        fixtures.CaptureV2Tests.setUp(self)

    def test_per_item_submit_and_skip_are_retired_for_every_protocol(self):
        attempt = self.attempts[Question.Type.SINGLE]
        for flow in (0, 1):
            self.session.assessment_flow_version = flow
            self.session.save(update_fields=['assessment_flow_version'])
            for route in ('submit', 'skip'):
                with self.subTest(flow=flow, route=route):
                    before = AssessmentEvent.objects.filter(session=self.session).count()
                    response = self.client.post(reverse(f'assessment_api:{route}', args=[attempt.pk]),
                        data=json.dumps({}), content_type='application/json')
                    self.assertEqual(response.status_code, 409, response.content)
                    self.assertIn('WHOLE_SESSION_SUBMISSION_REQUIRED', response.content.decode())
                    attempt.refresh_from_db()
                    self.assertEqual(attempt.status, 'unvisited')
                    self.assertEqual(AssessmentEvent.objects.filter(session=self.session).count(), before)
                    self.assertFalse(SubmissionEvent.objects.filter(attempt=attempt).exists())

    def test_legacy_finish_cannot_bypass_whole_paper_receipt(self):
        before = AssessmentEvent.objects.filter(session=self.session).count()
        response = self.client.post(reverse('assessment_api:finish', args=[self.session.pk]),
            data=json.dumps({}), content_type='application/json')
        self.assertEqual(response.status_code, 409, response.content)
        self.assertIn('WHOLE_SESSION_SUBMISSION_REQUIRED', response.content.decode())
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')
        self.assertEqual(AssessmentEvent.objects.filter(session=self.session).count(), before)
        self.assertFalse(SubmissionEvent.objects.filter(attempt__session=self.session).exists())

    def test_retired_routes_still_enforce_student_ownership(self):
        outsider = User.objects.create_user(username='retired-route-outsider', role=User.Role.STUDENT)
        from .test_support import accept_research_consent
        accept_research_consent(outsider)
        self.client.force_login(outsider)
        attempt = self.attempts[Question.Type.SINGLE]
        for route in ('submit', 'skip'):
            response = self.client.post(reverse(f'assessment_api:{route}', args=[attempt.pk]),
                data='{}', content_type='application/json')
            self.assertEqual(response.status_code, 404)
