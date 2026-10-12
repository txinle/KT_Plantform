from datetime import timedelta

from django.db.models import Max, Q
from django.utils import timezone

from .models import CourseEnrollment, CourseEnrollmentAccessPeriod


def open_enrollment_access(enrollment, *, at=None):
    at = at or timezone.now()
    current = enrollment.access_periods.filter(revoked_at__isnull=True).first()
    if current is not None:
        return current
    revision = (
        enrollment.access_periods.aggregate(value=Max("revision"))["value"] or 0
    ) + 1
    return CourseEnrollmentAccessPeriod.objects.create(
        enrollment=enrollment,
        course_id=enrollment.course_id,
        class_group_id=enrollment.class_group_id,
        student_id=enrollment.student_id,
        revision=revision,
        active_from=at,
    )


def close_enrollment_access(enrollment, *, at=None):
    at = at or timezone.now()
    current = enrollment.access_periods.filter(revoked_at__isnull=True).first()
    if current is None:
        current = CourseEnrollmentAccessPeriod.objects.create(
            enrollment=enrollment,
            course_id=enrollment.course_id,
            class_group_id=enrollment.class_group_id,
            student_id=enrollment.student_id,
            revision=(
                enrollment.access_periods.aggregate(value=Max("revision"))["value"]
                or 0
            )
            + 1,
            active_from=enrollment.enrolled_at,
        )
    if at <= current.active_from:
        at = current.active_from + timedelta(microseconds=1)
    current.revoked_at = at
    current.save(update_fields=["revoked_at"])
    return current


def has_course_access(*, course_id, student_id, at):
    periods = CourseEnrollmentAccessPeriod.objects.filter(
        course_id=course_id,
        student_id=student_id,
        active_from__lte=at,
    ).filter(Q(revoked_at__isnull=True) | Q(revoked_at__gt=at))
    if periods.exists():
        return True
    has_any_period = CourseEnrollmentAccessPeriod.objects.filter(
        course_id=course_id,
        student_id=student_id,
    ).exists()
    if has_any_period:
        return False
    return CourseEnrollment.objects.filter(
        course_id=course_id,
        student_id=student_id,
        is_active=True,
        enrolled_at__lte=at,
    ).exists()
from datetime import timedelta
