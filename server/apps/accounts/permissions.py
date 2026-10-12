from functools import wraps
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import JsonResponse


def role_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                if request.path.startswith('/api/v1/'):
                    return JsonResponse({'error': {'code': 'AUTHENTICATION_REQUIRED', 'message': '请重新登录。'}}, status=401)
                return login_required(view)(request, *args, **kwargs)
            if request.user.is_superuser or request.user.role in roles:
                return view(request, *args, **kwargs)
            raise PermissionDenied
        return wrapped
    return decorator
