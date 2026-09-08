from django.contrib import admin
from django.contrib.auth.views import LogoutView
from django.urls import include, path
from apps.accounts.views import PlatformLoginView, home, password_change
from apps.accounts.health import health

urlpatterns = [
    path("api/v1/", include("apps.api.urls")),
    path("health/", health, name="health"),
    path("", home, name="home"),
    path("login/", PlatformLoginView.as_view(), name="login"),
    path("password/change/", password_change, name="password-change"),
    path("logout/", LogoutView.as_view(), name="logout"),
    path("admin/", admin.site.urls),
    path("teacher/", include("apps.question_bank.urls")),
    path("teacher/courses/", include("apps.accounts.course_urls")),
    path("teacher/accounts/", include("apps.accounts.urls")),
    path("teacher/experiments/", include("apps.experiments.urls")),
    path("student/", include("apps.assessment.urls")),
    path("assessment/process/", include("apps.assessment.process_urls")),
    path("analytics/", include("apps.analytics.urls")),
]

admin.site.site_header = "KT Data Lab 管理后台"
admin.site.site_title = "KT Data Lab 管理后台"
admin.site.index_title = "平台数据管理"
