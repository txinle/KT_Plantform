from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.deprecation import MiddlewareMixin

from .password_services import password_change_required


class StudentPasswordChangeMiddleware(MiddlewareMixin):
    allowed_views = frozenset({"login", "logout", "password-change", "health", "api-health", "api-login", "api-logout", "api-session", "api-csrf", "api-password-change"})

    def process_view(self, request, view_func, view_args, view_kwargs):
        if not password_change_required(request.user):
            return None
        if request.resolver_match.view_name in self.allowed_views:
            return None
        if request.path.startswith(("/api/", "/assessment/process/")) or request.content_type == "application/json" or "application/json" in request.headers.get("Accept", ""):
            return JsonResponse({"error": {
                "code": "PASSWORD_CHANGE_REQUIRED", "message": "请先修改初始密码，再继续使用学生端。",
                "fields": {}, "request_id": getattr(request, "request_id", ""),
            }}, status=403)
        return redirect(reverse("password-change"))
