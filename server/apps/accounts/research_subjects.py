from __future__ import annotations


def _create_course_subjects(CourseSubject, subject_pairs):
    CourseSubject.objects.bulk_create(
        [
            CourseSubject(course_id=course_id, student_id=student_id)
            for course_id, student_id in sorted(subject_pairs)
        ],
        ignore_conflicts=True,
    )


def backfill_enrollment_course_subjects(apps, schema_editor):
    CourseEnrollment = apps.get_model("accounts", "CourseEnrollment")
    CourseSubject = apps.get_model("accounts", "CourseSubject")

    subject_pairs = set(
        CourseEnrollment.objects.values_list("course_id", "student_id").distinct()
    )
    _create_course_subjects(CourseSubject, subject_pairs)


def backfill_course_subjects(apps, schema_editor):
    CourseEnrollment = apps.get_model("accounts", "CourseEnrollment")
    CourseSubject = apps.get_model("accounts", "CourseSubject")
    ExperimentStudent = apps.get_model("experiments", "ExperimentStudent")
    ExperimentSession = apps.get_model("assessment", "ExperimentSession")

    subject_pairs = set(
        CourseEnrollment.objects.values_list("course_id", "student_id").distinct()
    )
    subject_pairs.update(
        ExperimentStudent.objects.filter(
            experiment__course_id__isnull=False,
        ).values_list("experiment__course_id", "student_id")
    )
    subject_pairs.update(
        ExperimentSession.objects.filter(
            version__experiment__course_id__isnull=False,
        ).values_list("version__experiment__course_id", "student_id")
    )
    _create_course_subjects(CourseSubject, subject_pairs)


def noop_reverse(apps, schema_editor):
    pass
