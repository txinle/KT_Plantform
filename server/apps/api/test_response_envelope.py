"""HTTP wire contract for the approved unified API boundary."""
import json

from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.test import RequestFactory, SimpleTestCase

from .middleware import RequestIdMiddleware


class ResponseEnvelopeTests(SimpleTestCase):
    def test_success_has_domain_data_and_matching_request_metadata(self):
        response = self.client.get('/api/v1/auth/csrf/')
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(set(body), {'data', 'meta'})
        self.assertTrue(body['data']['csrf_token'])
        self.assertEqual(body['meta']['request_id'], response['X-Request-ID'])

    def test_unknown_api_url_is_json_and_never_frontend_html(self):
        response = self.client.get('/api/v1/not-a-registered-endpoint/')
        self.assertEqual(response.status_code, 404)
        self.assertEqual(response['Content-Type'], 'application/json')
        error = response.json()['error']
        self.assertEqual(set(error), {'code', 'message', 'fields', 'request_id'})
        self.assertEqual(error['code'], 'NOT_FOUND')
        self.assertEqual(error['request_id'], response['X-Request-ID'])

    def test_html_negotiation_cannot_render_an_api_as_a_business_page(self):
        response = self.client.get('/api/v1/auth/csrf/', HTTP_ACCEPT='text/html')
        self.assertEqual(response.status_code, 406)
        self.assertEqual(response['Content-Type'], 'application/json')
        self.assertEqual(response.json()['error']['code'], 'NOT_ACCEPTABLE')

    def test_existing_api_error_remains_outside_success_envelope(self):
        response = self.client.post('/api/v1/auth/csrf/', '{}', content_type='application/json')
        self.assertEqual(response.status_code, 405)
        body = response.json()
        self.assertEqual(set(body), {'error'})
        self.assertEqual(body['error']['code'], 'METHOD_NOT_ALLOWED')
        self.assertEqual(body['error']['request_id'], response['X-Request-ID'])


class WireBoundaryTests(SimpleTestCase):
    def wrap(self, response):
        request = RequestFactory().get('/api/v1/student/attempts/1/draft/')
        return RequestIdMiddleware(lambda _: response)(request)

    def test_plain_json_receipt_is_wrapped_without_changing_evidence(self):
        receipt = {'success': True, 'acknowledged_event_ids': ['original-id'], 'version': 2}
        response = self.wrap(JsonResponse(receipt))
        self.assertEqual(json.loads(response.content)['data'], receipt)

    def test_legacy_conflict_preserves_specific_code_and_fields(self):
        response = self.wrap(JsonResponse({'success': False, 'error': {
            'code': 'navigation_response_version_conflict', 'fields': {'navigation_token': 'original'},
        }}, status=409))
        error = json.loads(response.content)['error']
        self.assertEqual(set(error), {'code', 'message', 'fields', 'request_id'})
        self.assertEqual(error['code'], 'navigation_response_version_conflict')
        self.assertEqual(error['fields'], {'navigation_token': 'original'})
        self.assertEqual(error['request_id'], response['X-Request-ID'])

    def test_unhandled_error_never_exposes_framework_html(self):
        response = self.wrap(HttpResponse('<html>private traceback</html>', status=500))
        self.assertEqual(json.loads(response.content)['error']['code'], 'SERVER_ERROR')
        self.assertNotIn(b'private traceback', response.content)

    def test_no_content_and_streaming_exports_remain_unchanged(self):
        empty = self.wrap(HttpResponse(status=204))
        self.assertEqual(empty.content, b'')
        exported = StreamingHttpResponse([b'csv-data'], content_type='text/csv')
        self.assertIs(self.wrap(exported), exported)
        self.assertEqual(b''.join(exported.streaming_content), b'csv-data')

    def test_json_download_is_not_wrapped(self):
        exported = JsonResponse({'raw_evidence': [1, 2]})
        exported['Content-Disposition'] = 'attachment; filename="evidence.json"'
        response = self.wrap(exported)
        self.assertEqual(json.loads(response.content), {'raw_evidence': [1, 2]})

    def test_openapi_describes_wire_envelope_and_leaves_non_json_responses(self):
        from .schema import envelope_responses
        content = {'$ref': '#/components/schemas/Receipt'}
        schema = {'paths': {'/api/v1/operation/': {'post': {'responses': {
            '200': {'content': {'application/json': {'schema': content}}},
            '204': {'description': 'No content'},
            '409': {'content': {'application/json': {'schema': {}}}},
        }}}}}
        result = envelope_responses(schema, None, None, True)
        responses = result['paths']['/api/v1/operation/']['post']['responses']
        success = responses['200']['content']['application/json']['schema']
        self.assertEqual(success['properties']['data'], content)
        self.assertEqual(success['required'], ['data', 'meta'])
        self.assertEqual(responses['204'], {'description': 'No content'})
        self.assertEqual(responses['409']['content']['application/json']['schema']['required'], ['error'])
