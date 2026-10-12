"""Document existing Django endpoints without introducing a second request pipeline."""
from drf_spectacular.openapi import AutoSchema
from drf_spectacular.utils import OpenApiParameter, OpenApiResponse, extend_schema
from rest_framework.views import APIView

from apps.api.authentication import ApiSessionAuthentication


class DomainMutationSchema(AutoSchema):
    def _get_request_body(self, direction='request'):
        body = super()._get_request_body(direction)
        if body is not None and self.view.body_required:
            body['required'] = True
        return body


def documented_domain_view(callback, *, request, responses, description, body_required=True):
    """DRF discovery only: Django retains CSRF, auth, body parsing and exception handling."""
    class DomainMutationView(APIView):
        schema = DomainMutationSchema()
        authentication_classes = [ApiSessionAuthentication]
        http_method_names = ['post']

        def dispatch(self, request, *args, **kwargs):
            # Pass the original HttpRequest before DRF reads/parses it. Existing
            # decorators/services remain the only runtime authority.
            return callback(request, *args, **kwargs)

        @extend_schema(
            operation_id='assessment_' + callback.__name__,
            tags=['Student mutations'], description=description,
            request={'application/json': request} if request is not None else None,
            responses={status: OpenApiResponse(schema, description=message)
                       for status, (schema, message) in responses.items()},
            parameters=[
                OpenApiParameter('X-CSRFToken', str, OpenApiParameter.HEADER,
                    description='Django CSRF token paired with the csrftoken cookie; required for JSON POST.'),
                OpenApiParameter('X-Request-ID', str, OpenApiParameter.HEADER,
                    description='Optional 32-character hexadecimal correlation ID.'),
            ],
        )
        def post(self, request, *args, **kwargs):
            return self.dispatch(request, *args, **kwargs)

    DomainMutationView.body_required = body_required
    view = DomainMutationView.as_view()
    # APIView.as_view exempts CSRF; retain Django middleware CSRF for these views.
    view.csrf_exempt = False
    if hasattr(callback, '_non_atomic_requests'):
        view._non_atomic_requests = callback._non_atomic_requests.copy()
    return view
