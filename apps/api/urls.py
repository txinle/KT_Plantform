from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView

from .views import CsrfTokenView, HealthView, SessionContextView


urlpatterns = [
    path("auth/session/", SessionContextView.as_view(), name="api-session"),
    path("auth/csrf/", CsrfTokenView.as_view(), name="api-csrf"),
    path("health/", HealthView.as_view(), name="api-health"),
    path("courses/", include("apps.accounts.api.urls")),
    path("teacher/", include("apps.experiments.api.urls")),
    path("teacher/", include("apps.analytics.api.urls")),
    path("teacher/", include("apps.question_bank.api.urls")),
    path("student/", include("apps.assessment.api.urls")),
    path("schema/", SpectacularAPIView.as_view(), name="api-schema"),
]
