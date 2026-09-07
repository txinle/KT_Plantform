from django.contrib.auth import authenticate, login, logout
from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.views import resolve_home_url

from .serializers import (
    CsrfTokenSerializer,
    HealthSerializer,
    LoginRequestSerializer,
    SessionContextSerializer,
)


def session_context(user):
    return {
        "user": {
            "id": user.pk,
            "role": user.role,
            "display_name": user.shown_name,
            "username": user.username,
            "student_number": user.student_number,
            "home_url": resolve_home_url(user),
        }
    }


def enforce_csrf(request):
    check = CSRFCheck(lambda request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF Failed: {reason}")


class SessionContextView(APIView):
    @extend_schema(responses=SessionContextSerializer)
    def get(self, request):
        return Response(session_context(request.user))


class CsrfTokenView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=CsrfTokenSerializer)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class LoginApiView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=LoginRequestSerializer, responses=SessionContextSerializer)
    def post(self, request):
        enforce_csrf(request._request)
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = authenticate(
            request=request._request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        if user is None:
            raise ValidationError({"non_field_errors": ["账号或密码不正确。"]})
        login(request._request, user)
        return Response(session_context(user))


class LogoutApiView(APIView):
    @extend_schema(request=None, responses={204: None})
    def post(self, request):
        logout(request._request)
        return Response(status=204)


class HealthView(APIView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=HealthSerializer)
    def get(self, request):
        return Response({"status": "ok"})
