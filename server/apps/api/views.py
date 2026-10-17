from django.contrib.auth import authenticate, login, logout
from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.middleware.csrf import get_token
from drf_spectacular.utils import extend_schema
from rest_framework.authentication import CSRFCheck
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView
from config.capture_timing import request_phase

from apps.accounts.account_queries import resolve_home_url
from apps.accounts.password_services import (
    acknowledge_initial_password_reminder,
    change_student_password,
)
from apps.accounts.models import User
from apps.accounts.research_consent import (
    CURRENT_RESEARCH_CONSENT_VERSION,
    RESEARCH_CONSENT_COPY,
    accept_current_research_consent,
    research_consent_required,
)
from apps.accounts.student_identity import StudentIdentityError
from .exceptions import ApiBusinessError

from .serializers import (
    BootstrapContextSerializer,
    CsrfTokenSerializer,
    HealthSerializer,
    LoginRequestSerializer,
    LoginContextSerializer,
    ResearchConsentAcceptanceSerializer,
    ResearchConsentDocumentSerializer,
    SessionContextSerializer,
    StudentPasswordChangeSerializer,
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
            "uses_initial_password": user.uses_initial_password,
            "initial_password_reminder_pending": user.initial_password_reminder_pending,
            "research_consent_required": research_consent_required(user),
            "research_consent_version": CURRENT_RESEARCH_CONSENT_VERSION,
        }
    }


def enforce_csrf(request):
    check = CSRFCheck(lambda request: None)
    check.process_request(request)
    reason = check.process_view(request, None, (), {})
    if reason:
        raise PermissionDenied(f"CSRF Failed: {reason}")


def client_navigation(user):
    """Project the server rollout boundary; API authorization still applies per read/write."""
    courses = settings.STUDENT_COURSE_NAVIGATION_COURSE_IDS
    users = settings.STUDENT_COURSE_NAVIGATION_USER_IDS
    enabled = (settings.STUDENT_COURSE_NAVIGATION_ENABLED and user.is_authenticated
               and user.role == User.Role.STUDENT)
    all_courses = enabled and ('*' in users or str(user.pk) in users or '*' in courses)
    course_ids = ['*'] if all_courses else sorted(value for value in courses if value.isdecimal()) if enabled else []
    return {'student_courses': bool(course_ids), 'course_ids': course_ids}


class AuthenticationView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'no-store'
        return response


class BootstrapContextView(AuthenticationView):
    permission_classes = [AllowAny]

    @extend_schema(responses=BootstrapContextSerializer)
    def get(self, request):
        user = session_context(request.user)['user'] if request.user.is_authenticated else None
        return Response({'csrf_token': get_token(request), 'user': user, 'client_navigation': client_navigation(request.user)})


class SessionContextView(AuthenticationView):
    @extend_schema(responses=SessionContextSerializer)
    def get(self, request):
        return Response(session_context(request.user))


class CsrfTokenView(AuthenticationView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(responses=CsrfTokenSerializer)
    def get(self, request):
        return Response({"csrf_token": get_token(request)})


class LoginApiView(AuthenticationView):
    authentication_classes = []
    permission_classes = [AllowAny]

    @extend_schema(request=LoginRequestSerializer, responses=LoginContextSerializer)
    def post(self, request):
        enforce_csrf(request._request)
        serializer = LoginRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        with request_phase(request, 'login_authenticate'):
            user = authenticate(
                request=request._request,
                username=serializer.validated_data["username"],
                password=serializer.validated_data["password"],
            )
        if user is None:
            raise ValidationError({"non_field_errors": ["账号或密码不正确。"]})
        with request_phase(request, 'login_session'):
            login(request._request, user)
        with request_phase(request, 'login_projection'):
            context = {**session_context(user), 'csrf_token': get_token(request._request), 'client_navigation': client_navigation(user)}
        return Response(context)


class StudentPasswordChangeView(AuthenticationView):
    @extend_schema(request=StudentPasswordChangeSerializer, responses=SessionContextSerializer)
    def post(self, request):
        serializer = StudentPasswordChangeSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            student = change_student_password(request=request._request, data=serializer.validated_data)
        except StudentIdentityError as exc:
            raise ApiBusinessError("；".join(exc.messages), code=exc.business_code, fields=exc.fields, status=exc.status_code) from exc
        except DjangoValidationError as exc:
            raise ValidationError(exc.message_dict if hasattr(exc, "message_dict") else exc.messages) from exc
        return Response(session_context(student))


class InitialPasswordReminderShownView(AuthenticationView):
    @extend_schema(request=None, responses=SessionContextSerializer)
    def post(self, request):
        try:
            student = acknowledge_initial_password_reminder(request=request._request)
        except StudentIdentityError as exc:
            raise ApiBusinessError(
                "；".join(exc.messages),
                code=exc.business_code,
                fields=exc.fields,
                status=exc.status_code,
            ) from exc
        return Response(session_context(student))


class ResearchConsentView(AuthenticationView):
    @staticmethod
    def _require_student(request):
        if request.user.is_superuser or request.user.role != User.Role.STUDENT:
            raise PermissionDenied("仅学生账号可以确认研究参与说明。")

    @extend_schema(responses=ResearchConsentDocumentSerializer)
    def get(self, request):
        self._require_student(request)
        return Response(
            {"version": CURRENT_RESEARCH_CONSENT_VERSION, **RESEARCH_CONSENT_COPY}
        )

    @extend_schema(
        request=ResearchConsentAcceptanceSerializer,
        responses=SessionContextSerializer,
    )
    def post(self, request):
        self._require_student(request)
        serializer = ResearchConsentAcceptanceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        version = serializer.validated_data["version"]
        if version != CURRENT_RESEARCH_CONSENT_VERSION:
            raise ApiBusinessError(
                "研究参与说明已更新，请重新阅读后确认。",
                code="RESEARCH_CONSENT_VERSION_STALE",
                status=409,
            )
        accept_current_research_consent(student=request.user, version=version)
        return Response(session_context(request.user))


class LogoutApiView(AuthenticationView):
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
