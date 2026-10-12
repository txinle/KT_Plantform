"""Session-authorized binary delivery with the existing download domain contract."""

from drf_spectacular.utils import OpenApiParameter, extend_schema
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import User
from .permissions import RolePermission


CSV = 'text/csv'
XLSX = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
DOCX = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'


def download_view(callback, *, method='get', media_types=(CSV,), operation_id, request_schema=None, kwarg_map=None):
    """Preserve raw form/streaming semantics; DRF supplies role, CSRF and errors."""
    def deliver(self, request, *args, **kwargs):
        mapped = {(kwarg_map or {}).get(key, key): value for key, value in kwargs.items()}
        response = callback(request._request, *args, **mapped)
        if response.status_code >= 400 and not response.streaming:
            return Response({'detail': response.content.decode(response.charset)}, status=response.status_code)
        return response

    responses = {(200, mime): bytes for mime in media_types}
    responses.update({code: {'type': 'object'} for code in (400, 401, 403, 404)})
    parameters = [OpenApiParameter('X-CSRFToken', str, OpenApiParameter.HEADER,
                                  description='Django CSRF token, required for unsafe methods.')]
    deliver = extend_schema(operation_id=operation_id, request=request_schema,
                            responses=responses, parameters=parameters)(deliver)
    view = type(f'{callback.__name__.title().replace("_", "")}DownloadView', (APIView,), {
        '__module__': __name__, 'permission_classes': (RolePermission,),
        'allowed_roles': (User.Role.TEACHER,), 'http_method_names': (method, 'options'), method: deliver,
    })
    return view.as_view()
