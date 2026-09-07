from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from .views import (
    CsrfTokenView,
    HealthView,
    LoginApiView,
    LogoutApiView,
    SessionContextView,
)


urlpatterns = [
    path("auth/session/", SessionContextView.as_view(), name="api-session"),
    path("auth/csrf/", CsrfTokenView.as_view(), name="api-csrf"),
    path("auth/login/", LoginApiView.as_view(), name="api-login"),
    path("auth/logout/", LogoutApiView.as_view(), name="api-logout"),
    path("health/", HealthView.as_view(), name="api-health"),
    path("courses/", include("apps.accounts.api.urls")),
    path("teacher/courses/", include("apps.accounts.api.teacher_urls")),
    path("teacher/accounts/", include("apps.accounts.api.teacher_account_management_urls")),
    path("teacher/", include("apps.experiments.api.urls")),
    path("teacher/", include("apps.analytics.api.urls")),
    path("teacher/", include("apps.question_bank.api.urls")),
    path("student/", include("apps.assessment.api.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
]
