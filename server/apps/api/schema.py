from drf_spectacular.extensions import OpenApiAuthenticationExtension


def envelope_responses(result, generator, request, public):
    for path in result.get('paths', {}).values():
        for operation in path.values():
            if not isinstance(operation, dict):
                continue
            for status, response in operation.get('responses', {}).items():
                document = response.get('content', {}).get('application/json')
                if document is None or status in {'204', '304'}:
                    continue
                if status.startswith('2'):
                    document['schema'] = {
                        'type': 'object', 'required': ['data', 'meta'],
                        'properties': {
                            'data': document.get('schema', {}),
                            'meta': {'type': 'object', 'required': ['request_id'],
                                     'properties': {'request_id': {'type': 'string'}}},
                        },
                    }
                elif status.startswith(('4', '5')):
                    document['schema'] = {
                        'type': 'object', 'required': ['error'],
                        'properties': {'error': {
                            'type': 'object', 'required': ['code', 'message', 'fields', 'request_id'],
                            'properties': {
                                'code': {'type': 'string'}, 'message': {'type': 'string'},
                                'fields': {'type': 'object', 'additionalProperties': {}},
                                'request_id': {'type': 'string'},
                            },
                        }},
                    }
    return result


class ApiSessionAuthenticationScheme(OpenApiAuthenticationExtension):
    target_class = "apps.api.authentication.ApiSessionAuthentication"
    name = "SessionAuth"

    def get_security_definition(self, auto_schema):
        return {
            "type": "apiKey",
            "in": "cookie",
            "name": "sessionid",
            "description": "Django same-origin session. Unsafe methods also require X-CSRFToken.",
        }
