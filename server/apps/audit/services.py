from .models import AuditLog


def log_action(request, action, obj=None, details=None):
    object_type = obj.__class__.__name__ if obj is not None else "system"
    object_id = str(getattr(obj, "pk", "") or "")
    forwarded = request.META.get("HTTP_X_FORWARDED_FOR", "")
    ip_address = forwarded.split(",")[0].strip() if forwarded else request.META.get("REMOTE_ADDR")
    return AuditLog.objects.create(
        actor=request.user if request.user.is_authenticated else None,
        action=action,
        object_type=object_type,
        object_id=object_id,
        details=details or {},
        ip_address=ip_address or None,
    )
