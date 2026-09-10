from ipaddress import ip_address
from urllib.parse import urlsplit

from django.conf import settings
from django.http import HttpResponse


def _is_loopback_host(hostname):
    if hostname == "localhost":
        return True
    try:
        return ip_address(hostname).is_loopback
    except ValueError:
        return False


def _normalized_origin(parts):
    scheme = parts.scheme.lower()
    port = parts.port
    if port is None:
        port = 443 if scheme == "https" else 80 if scheme == "http" else None
    return scheme, (parts.hostname or "").lower(), port


class CanonicalPublicOriginMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        canonical_origin = settings.CANONICAL_PUBLIC_ORIGIN
        if (
            not canonical_origin
            or request.method not in {"GET", "HEAD"}
            or request.path == "/health/"
        ):
            return self.get_response(request)

        incoming = urlsplit(f"{request.scheme}://{request.get_host()}")
        canonical = urlsplit(canonical_origin)
        incoming_hostname = incoming.hostname or ""
        if (
            _normalized_origin(incoming) == _normalized_origin(canonical)
            or _is_loopback_host(incoming_hostname.lower())
        ):
            return self.get_response(request)

        return HttpResponse(
            status=308,
            headers={"Location": f"{canonical_origin}{request.get_full_path()}"},
        )


class StudentSessionIdleTimeoutMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    @staticmethod
    def _renew_student_session(request):
        user = getattr(request, "user", None)
        if (
            user is not None
            and user.is_authenticated
            and not user.is_superuser
            and user.role == "student"
        ):
            request.session.set_expiry(
                settings.STUDENT_SESSION_IDLE_TIMEOUT_SECONDS
            )

    def __call__(self, request):
        self._renew_student_session(request)
        response = self.get_response(request)
        self._renew_student_session(request)
        return response
