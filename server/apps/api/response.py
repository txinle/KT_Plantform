"""The JSON wire boundary shared by DRF and assessment domain adapters."""
import json
from http import HTTPStatus

from .exceptions import STATUS_CODES


def apply_api_envelope(request, response):
    if (response.streaming or response.status_code in {204, 304}
            or response.has_header('Content-Disposition')
            or request.path == '/api/v1/schema/'):
        return response
    is_json = response.get('Content-Type', '').split(';', 1)[0] == 'application/json'
    document = json.loads(response.content) if is_json and response.content else None
    if 200 <= response.status_code < 300:
        if not is_json:
            return response
        body = {'data': document, 'meta': {'request_id': request.request_id}}
    elif response.status_code >= 400:
        original = document.get('error') if isinstance(document, dict) else None
        detail = original if isinstance(original, dict) else {}
        default_code = 'SERVER_ERROR' if response.status_code >= 500 else STATUS_CODES.get(response.status_code, 'API_ERROR')
        default_message = HTTPStatus(response.status_code).phrase
        body = {'error': {
            'code': detail.get('code') or (document.get('code') if isinstance(document, dict) else None) or default_code,
            'message': detail.get('message') or (original if isinstance(original, str) else None) or default_message,
            'fields': detail.get('fields') or {},
            'request_id': request.request_id,
        }}
    else:
        return response
    response.content = json.dumps(body, ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    response['Content-Type'] = 'application/json'
    if response.has_header('Content-Length'):
        response['Content-Length'] = str(len(response.content))
    return response
