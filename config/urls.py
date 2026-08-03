from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from django.urls import include, path
from apps.accounts.views import home
from apps.accounts.health import health

urlpatterns = [
    path("health/", health, name="health"),
    path("", home, name="home"),
    path("login/", LoginView.as_view(template_name="registration/login.html"), name="login"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("teacher/", include("apps.question_bank.urls")),
    path("teacher/accounts/", include("apps.accounts.urls")),
    path("teacher/experiments/", include("apps.experiments.urls")),
    path("student/", include("apps.assessment.urls")),
    path("analytics/", include("apps.analytics.urls")),
]
