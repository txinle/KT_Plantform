from django.db.models import Q

from apps.audit.models import AuditLog


def course_revision_value(course):
    latest_audit = (
        AuditLog.objects.filter(
            Q(details__course_id=course.pk)
            | Q(object_type="Course", object_id=str(course.pk))
        )
        .order_by("-created_at", "-pk")
        .values_list("created_at", "pk")
        .first()
    )
    audit_marker = (
        f"{latest_audit[0].isoformat()}:{latest_audit[1]}" if latest_audit else "0"
    )
    return f"{course.updated_at.isoformat()}:{audit_marker}"
