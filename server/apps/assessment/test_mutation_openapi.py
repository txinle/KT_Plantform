"""The documented adapters must preserve the existing Django mutation boundary."""
import json
import uuid

from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.middleware.csrf import _get_new_csrf_string
from django.urls import include, path, resolve, reverse
from django.utils import timezone
from drf_spectacular.generators import SchemaGenerator
from drf_spectacular.validation import validate_schema
from jsonschema import Draft4Validator

from apps.question_bank.models import Question
from . import test_capture_v2 as fixtures
from .models import AssessmentEvent
from . import test_atomic_submission as atomic_fixtures
from . import test_saved_navigation as navigation_fixtures
from . import process_urls


PATTERNS = [
    path('api/v1/student/', include('apps.assessment.api.mutation_urls')),
    path('api/v1/assessment/process/', include('apps.assessment.process_urls')),
]
STUDENT_PATHS = [
    'attempts/{attempt_id}/draft/', 'attempts/{attempt_id}/submit/',
    'attempts/{attempt_id}/skip/', 'sessions/{session_id}/finish/',
]
PROCESS_PATHS = [
    'capture-v2/attempts/{attempt_id}/tools/', 'capture-v2/attempts/{attempt_id}/resolve/',
    'capture-v2/batch/', 'events/batch/', 'capture-checkpoints/',
    'time-sync/create/', 'time-sync/finalize/',
    'attempts/{attempt_id}/writer-lease/acquire/', 'attempts/{attempt_id}/writer-lease/renew/',
    'attempts/{attempt_id}/writer-lease/release/', 'attempts/{attempt_id}/confidence/prompt-shown/',
    'attempts/{attempt_id}/confidence/commit/', 'attempts/{attempt_id}/confidence/skip/',
]


def json_schema(schema):
    """Translate OpenAPI 3.0 nullable into JSON Schema for example verification."""
    if isinstance(schema, list):
        return [json_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    result = {key: json_schema(value) for key, value in schema.items() if key != 'nullable'}
    return {'anyOf': [result, {'type': 'null'}]} if schema.get('nullable') else result


class MutationOpenApiTests(SimpleTestCase):
    def test_runtime_actions_do_not_advertise_retired_writes(self):
        from .api.serializers import StudentRuntimeActionsSerializer
        fields = StudentRuntimeActionsSerializer().fields
        self.assertNotIn('submit_url', fields)
        self.assertNotIn('skip_url', fields)

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document = SchemaGenerator(patterns=PATTERNS).get_schema(request=None, public=True)

    def operation(self, prefix, suffix):
        endpoint = prefix + suffix
        self.assertIn(endpoint, self.document['paths'])
        self.assertEqual(set(self.document['paths'][endpoint]), {'post'})
        return self.document['paths'][endpoint]['post']

    def test_openapi_document_is_valid(self):
        validate_schema(self.document)

    def test_every_mutation_is_post_only_with_security_headers_and_wire_envelopes(self):
        for prefix, suffixes in [('/api/v1/student/', STUDENT_PATHS),
                                  ('/api/v1/assessment/process/', PROCESS_PATHS)]:
            for suffix in suffixes:
                with self.subTest(endpoint=prefix + suffix):
                    operation = self.operation(prefix, suffix)
                    self.assertEqual(operation['security'], [{'SessionAuth': []}])
                    headers = {p['name'] for p in operation['parameters'] if p['in'] == 'header'}
                    self.assertTrue({'X-CSRFToken', 'X-Request-ID'} <= headers)
                    for status in ('401', '403', '405'):
                        self.assertIn(status, operation['responses'])
                    for status, response in operation['responses'].items():
                        schema = response['content']['application/json']['schema']
                        if status.startswith('2'):
                            self.assertEqual(schema['required'], ['data', 'meta'])
                            self.assertNotIn('data', schema['properties']['data'].get('properties', {}))
                        else:
                            self.assertEqual(schema['required'], ['error'])

    def test_draft_documents_the_actual_bundle_and_receipt_fields(self):
        for action, bundle_name, receipt_fields in [
            ('draft', 'save_and_leave', {'navigation_token', 'writer_lease_released', 'to_assignment_id'}),
        ]:
            operation = self.operation('/api/v1/student/', 'attempts/{attempt_id}/' + action + '/')
            request = operation['requestBody']['content']['application/json']['schema']
            bundle_variant = next(item for item in request['oneOf'] if bundle_name in item['properties'])
            bundle = bundle_variant['properties'][bundle_name]
            self.assertEqual(set(bundle['required']), {'version', 'client_instance_id', 'event_boundary',
                                                       'events', 'checkpoints', 'expected_version'})
            self.assertEqual(bundle['properties']['events']['maxItems'], 100)
            self.assertEqual(bundle['properties']['checkpoints']['maxItems'], 100)
            response = operation['responses']['200']['content']['application/json']['schema']['properties']['data']
            receipt = next(item for item in response.get('oneOf', [response])
                           if receipt_fields <= set(item['properties']))
            self.assertTrue(receipt_fields <= set(receipt['properties']))
            self.assertTrue(receipt_fields <= set(receipt['required']))

    def test_capture_documents_event_clocks_and_versioned_payload_semantics(self):
        operation = self.operation('/api/v1/assessment/process/', 'capture-v2/batch/')
        body = operation['requestBody']['content']['application/json']['schema']
        flat, bundled = body['oneOf']
        event = flat['properties']['events']['items']
        self.assertEqual(bundled['required'], ['capture_bundle'])
        self.assertEqual(bundled['properties']['capture_bundle']['properties']['events']['items'], event)
        self.assertTrue({'event_type', 'event_version', 'payload', 'client_wall_time_utc',
                         'client_monotonic_ms', 'client_time_origin'} <= set(event['required']))
        self.assertIn('event_schema_registry', event['properties']['payload']['description'])
        self.assertIn('413', operation['responses'])
        self.assertIn('415', operation['responses'])

    def test_retired_submission_and_skip_have_no_success_or_request_body(self):
        for action in ('submit', 'skip'):
            operation = self.operation('/api/v1/student/', 'attempts/{attempt_id}/' + action + '/')
            self.assertNotIn('requestBody', operation)
            self.assertFalse(any(code.startswith('2') for code in operation['responses']))
            self.assertIn('409', operation['responses'])
        finish = self.operation('/api/v1/student/', 'sessions/{session_id}/finish/')
        self.assertTrue(finish['requestBody']['required'])
        self.assertNotIn('200', finish['responses'])
        self.assertIn('202', finish['responses'])

    def test_json_process_bodies_are_required_and_obsolete_confidence_has_no_success(self):
        for suffix in PROCESS_PATHS:
            operation = self.operation('/api/v1/assessment/process/', suffix)
            if suffix.endswith(('confidence/commit/', 'confidence/skip/')):
                self.assertNotIn('requestBody', operation)
                self.assertFalse(any(code.startswith('2') for code in operation['responses']))
            else:
                self.assertTrue(operation['requestBody']['required'])


@override_settings(ATOMIC_SUBMISSION_ENABLED=True, ATOMIC_SUBMISSION_USER_IDS=frozenset({'*'}),
                   SAVE_AND_LEAVE_ENABLED=True, SAVE_AND_LEAVE_USER_IDS=frozenset({'*'}))
class MutationAdapterHttpTests(TestCase):
    setUp = fixtures.CaptureV2Tests.setUp
    transition = fixtures.CaptureV2Tests.transition

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.document = SchemaGenerator(patterns=PATTERNS).get_schema(request=None, public=True)

    def validate_example(self, suffix, request, response=None):
        operation = self.document['paths']['/api/v1/student/attempts/{attempt_id}/' + suffix + '/']['post']
        body = operation['requestBody']['content']['application/json']['schema']
        Draft4Validator(json_schema(body)).validate(request)
        if response is not None:
            schema = operation['responses']['200']['content']['application/json']['schema']
            Draft4Validator(json_schema(schema)).validate(response.json())

    def post(self, name, document, args=(), client=None, **extra):
        return (client or self.client).post(reverse(name, args=args),
            data=json.dumps(document), content_type='application/json', **extra)

    def csrf_client(self, user=None):
        client = Client(enforce_csrf_checks=True)
        if user is not None:
            client.force_login(user)
        token = _get_new_csrf_string()
        client.cookies['csrftoken'] = token
        return client, token

    def test_anonymous_and_wrong_role_preserve_401_and_403_for_every_route(self):
        names = [('assessment_api:' + name, [1]) for name in ('save_draft', 'submit', 'skip', 'finish')]
        names += [('assessment_process:' + p.name, [1] if '<int:' in str(p.pattern) else [])
                  for p in process_urls.urlpatterns]
        for user, expected in [(None, 401), (self.teacher, 403)]:
            client, token = self.csrf_client(user)
            for name, args in names:
                with self.subTest(name=name, status=expected):
                    response = self.post(name, {}, args, client, HTTP_X_CSRFTOKEN=token)
                    self.assertEqual(response.status_code, expected)
                    self.assertEqual(set(response.json()), {'error'})

    def test_csrf_remains_required_and_valid_post_reaches_original_json_validation(self):
        client, token = self.csrf_client(self.student)
        names = [('assessment_api:' + name, [1]) for name in ('save_draft', 'submit', 'skip', 'finish')]
        names += [('assessment_process:' + p.name, [1] if '<int:' in str(p.pattern) else [])
                  for p in process_urls.urlpatterns]
        for name, args in names:
            with self.subTest(csrf=name):
                self.assertEqual(self.post(name, {}, args, client).status_code, 403)
        for name, args in [('assessment_api:submit', [self.attempts[Question.Type.SINGLE].pk]),
                            ('assessment_process:capture_v2_batch', [])]:
            with self.subTest(name=name):
                self.assertEqual(self.post(name, {}, args, client).status_code, 403)
                response = self.post(name, {}, args, client, HTTP_X_CSRFTOKEN=token)
                self.assertEqual(response.status_code, 409 if name == 'assessment_api:submit' else 400)
                self.assertEqual(set(response.json()), {'error'})

    def test_non_atomic_flags_and_no_csrf_exemption_survive_adapter(self):
        for name in ('save_draft',):
            callback = resolve(reverse('assessment_api:' + name, args=[1])).func
            self.assertEqual(callback._non_atomic_requests, {'default'})
            self.assertFalse(getattr(callback, 'csrf_exempt', False))

    def test_all_endpoints_remain_post_only_at_runtime(self):
        self.client.force_login(self.student)
        names = [('assessment_api:' + name, [1]) for name in ('save_draft', 'submit', 'skip', 'finish')]
        names += [('assessment_process:' + p.name, [1] if '<int:' in str(p.pattern) else [])
                  for p in process_urls.urlpatterns]
        for name, args in names:
            for method in ('get', 'head', 'options'):
                with self.subTest(name=name, method=method):
                    self.assertEqual(getattr(self.client, method)(reverse(name, args=args)).status_code, 405)

    def test_skip_and_legacy_finish_are_retired_without_domain_transitions(self):
        self.client.force_login(self.student)
        attempt = self.attempts[Question.Type.SINGLE]
        for name, identity in [('skip', attempt.pk), ('finish', self.session.pk)]:
            response = self.post('assessment_api:' + name, {}, [identity])
            self.assertEqual(response.status_code, 409, response.content)
            self.assertEqual(set(response.json()), {'error'})
            self.assertEqual(response.json()['error']['code'], 'WHOLE_SESSION_SUBMISSION_REQUIRED')
        self.session.refresh_from_db()
        self.assertEqual(self.session.status, 'in_progress')

    def test_process_content_type_errors_match_existing_endpoint_statuses(self):
        self.client.force_login(self.student)
        for name, args, status in [('capture_v2_batch', [], 415), ('event_batch', [], 415),
                                   ('research_tools', [self.attempts[Question.Type.SINGLE].pk], 409),
                                   ('resolve_branch', [self.attempts[Question.Type.SINGLE].pk], 409)]:
            with self.subTest(name=name):
                response = self.client.post(reverse('assessment_process:' + name, args=args),
                                            data='{}', content_type='text/plain')
                self.assertEqual(response.status_code, status)
                self.assertEqual(response.json()['error']['code'], 'json_required')

    def test_capture_post_uses_original_arrival_clock_and_returns_one_wire_envelope(self):
        self.client.force_login(self.student)
        attempt = self.attempts[Question.Type.SINGLE]
        self.stream = uuid.uuid4()
        event = self.transition(attempt, None, 'A')
        before = timezone.now()
        response = self.post('assessment_process:capture_v2_batch',
                             {'client_instance_id': str(self.stream), 'events': [event]})
        after = timezone.now()
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(set(response.json()), {'data', 'meta'})
        ack = response.json()['data']['acknowledgements'][0]
        self.assertEqual(ack['event_id'], event['event_id'])
        recorded = AssessmentEvent.objects.get(pk=event['event_id'])
        self.assertLessEqual(before, recorded.server_received_at)
        self.assertLessEqual(recorded.server_received_at, after)
        oversized = self.post('assessment_process:capture_v2_batch', {}, CONTENT_LENGTH=str(8 * 1024 * 1024 + 1))
        self.assertEqual(oversized.status_code, 413)
        self.assertEqual(oversized.json()['error']['code'], 'request_too_large')

    def test_navigation_preserves_real_receipt_and_replay(self):
        self.client.force_login(self.student)
        attempt, navigation = navigation_fixtures.SavedNavigationTests.bundle(self)
        self.validate_example('draft', navigation)
        first = self.post('assessment_api:save_draft', navigation, [attempt.pk])
        self.assertEqual(first.status_code, 200, first.content)
        replay = self.post('assessment_api:save_draft', navigation, [attempt.pk])
        self.assertEqual(replay.json()['data'], first.json()['data'])
        self.assertTrue(first.json()['data']['writer_lease_released'])
        self.validate_example('draft', navigation, first)

    def test_old_atomic_submission_is_rejected_without_ingesting_material(self):
        self.client.force_login(self.student)
        attempt, submission = atomic_fixtures.AtomicSubmissionTests.bundle(self)
        before = AssessmentEvent.objects.count()
        response = self.post('assessment_api:submit', submission, [attempt.pk])
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()['error']['code'], 'WHOLE_SESSION_SUBMISSION_REQUIRED')
        self.assertEqual(AssessmentEvent.objects.count(), before)
