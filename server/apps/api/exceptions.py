from rest_framework.exceptions import APIException
from rest_framework.views import exception_handler


def csrf_failure(request, reason=''):
    if request.path.startswith('/api/v1/'):
        from django.http import JsonResponse
        return JsonResponse({'error': {'code': 'CSRF_FAILED', 'message': '请求校验失败，请刷新页面后重试。'}}, status=403)
    from django.views.csrf import csrf_failure as django_csrf_failure
    return django_csrf_failure(request, reason=reason)


STATUS_CODES = {
    400: "VALIDATION_ERROR",
    401: "AUTHENTICATION_REQUIRED",
    403: "PERMISSION_DENIED",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    406: "NOT_ACCEPTABLE",
    409: "CONFLICT",
}


class ApiBusinessError(APIException):
    status_code = 409
    default_code = "CONFLICT"

    def __init__(self, detail, *, code="CONFLICT", fields=None, status=409):
        self.status_code = status
        self.default_code = code
        self.fields = fields or {}
        super().__init__(detail=detail, code=code)


class ApiConflict(ApiBusinessError):
    pass


def api_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is None:
        return None
    request = context.get("request")
    detail = response.data
    if isinstance(exc, ApiBusinessError):
        message = (
            str(detail["detail"])
            if isinstance(detail, dict) and set(detail) == {"detail"}
            else str(detail)
        )
        fields = exc.fields
    elif isinstance(detail, dict) and set(detail) == {"detail"}:
        message = str(detail["detail"])
        fields = {}
    elif isinstance(detail, dict):
        message = "请求数据无效。"
        fields = detail
    elif isinstance(detail, (list, tuple)):
        message = "；".join(str(item) for item in detail)
        fields = {}
    else:
        message = str(detail)
        fields = {}
    code = (
        getattr(exc, "default_code", "CONFLICT")
        if isinstance(exc, ApiBusinessError)
        else STATUS_CODES.get(response.status_code, "API_ERROR")
    )
    if response.status_code == 403 and message.startswith("CSRF Failed:"):
        code = "CSRF_FAILED"
    response.data = {
        "error": {
            "code": code,
            "message": message,
            "fields": fields,
            "request_id": getattr(request, "request_id", ""),
        }
    }
    return response
