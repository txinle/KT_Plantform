from apps.audit.models import AuditLog

from .models import CourseEnrollment


def _recorded_removals(student_id):
    return AuditLog.objects.filter(
        action="student.course_remove", object_type="User", object_id=str(student_id),
        details__student_id=student_id,
    )


def has_course_membership_history(course_id, student_id):
    if CourseEnrollment.objects.filter(course_id=course_id, student_id=student_id).exists():
        return True
    return _recorded_removals(student_id).filter(details__course_id=course_id).exists()


def has_other_course_membership_history(course_id, student_id):
    if CourseEnrollment.objects.filter(student_id=student_id).exclude(course_id=course_id).exists():
        return True
    return _recorded_removals(student_id).filter(details__course_id__isnull=False).exclude(details__course_id=course_id).exists()
