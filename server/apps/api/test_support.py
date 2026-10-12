"""Assert the HTTP contract before domain assertions inspect a response."""


def domain_payload(response):
    body = response.json()
    if (not response.request['PATH_INFO'].startswith('/api/v1/')
            or response.request['PATH_INFO'] == '/api/v1/schema/'
            or response.has_header('Content-Disposition')):
        return body
    if 200 <= response.status_code < 300:
        assert set(body) == {'data', 'meta'}, body
        assert body['meta'] == {'request_id': response['X-Request-ID']}
        return body['data']
    assert set(body) == {'error'}, body
    assert set(body['error']) == {'code', 'message', 'fields', 'request_id'}, body
    assert body['error']['request_id'] == response['X-Request-ID']
    return body
