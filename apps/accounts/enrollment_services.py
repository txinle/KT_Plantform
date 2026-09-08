from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .class_lifecycle import lock_active_class_groups
from .course_access import lock_writable_course
from .models import ClassGroup, CourseEnrollment, User
from .student_import import StudentImportError


TARGET_CLASS_INACTIVE_ERROR = "目标班级已停用，不能新增学生。"


def enroll_single_student(course, class_group, student_number, display_name, *, request=None, confirmation_token=None):
    from .student_identity import enroll_student
    return enroll_student(
        course, class_group, student_number, display_name,
        request=request, confirmation_token=confirmation_token,
    )


@transaction.atomic
def copy_class_enrollments(*, target_course, target_class, source_class):
    if target_class.course_id != target_course.pk:
        raise ValidationError("目标班级必须属于目标课程")
    if source_class.course_id == target_course.pk:
        raise ValidationError("来源班级必须属于其他课程")
    with lock_active_class_groups(
        [target_class],
        inactive_error=TARGET_CLASS_INACTIVE_ERROR,
        lock_courses=True,
    ) as locked_classes:
        target_class = locked_classes[target_class.pk]
        if target_class.course_id != target_course.pk:
            raise ValidationError("目标班级必须属于目标课程")
        source_student_ids = list(
            source_class.enrollments.filter(is_active=True).values_list("student_id", flat=True)
        )
        existing_ids = set(
            CourseEnrollment.objects.filter(
                course=target_course,
                student_id__in=source_student_ids,
            ).values_list("student_id", flat=True)
        )
        created = 0
        skipped = 0
        for student_id in source_student_ids:
            if student_id in existing_ids:
                skipped += 1
                continue
            _, was_created = CourseEnrollment.objects.get_or_create(
                course=target_course,
                student_id=student_id,
                defaults={"class_group": target_class, "is_active": True},
            )
            if was_created:
                created += 1
            else:
                skipped += 1
        return created, skipped


@transaction.atomic
def enroll_parsed_students(course, class_group, parsed_students):
    if class_group.course_id != course.pk:
        raise ValidationError("班级必须属于当前课程")
    with lock_active_class_groups(
        [class_group],
        inactive_error=TARGET_CLASS_INACTIVE_ERROR,
        lock_courses=True,
    ) as locked_classes:
        class_group = locked_classes[class_group.pk]
        if class_group.course_id != course.pk:
            raise ValidationError("班级必须属于当前课程")
        numbers = [item.student_number for item in parsed_students]
        if len(numbers) != len(set(numbers)):
            raise ValidationError("导入文件中存在重复学号")

        existing = {
            value: user
            for user in User.objects.filter(
                Q(student_number__in=numbers) | Q(username__in=numbers)
            )
            for value in (user.student_number, user.username)
            if value in numbers
        }
        for item in parsed_students:
            if item.class_id != class_group.pk or item.class_name != str(class_group):
                raise ValidationError(f"班级不属于当前导入目标：{item.class_name}")
            user = existing.get(item.student_number)
            if user is None:
                continue
            if user.role != User.Role.STUDENT:
                raise ValidationError(f"学号与非学生账号冲突：{item.student_number}")
            if user.shown_name != item.display_name:
                raise ValidationError(f"学号对应姓名不一致：{item.student_number}")
            if CourseEnrollment.objects.filter(course=course, student=user).exists():
                raise ValidationError(f"学生已加入当前课程：{item.student_number}")

        created_accounts = 0
        enrollments = 0
        for item in parsed_students:
            student = existing.get(item.student_number)
            if student is None:
                student = User.objects.create_user(
                    username=item.student_number,
                    student_number=item.student_number,
                    display_name=item.display_name,
                    role=User.Role.STUDENT,
                    password=f"S{item.student_number}",
                    must_change_password=True,
                )
                created_accounts += 1
            CourseEnrollment.objects.create(
                course=course,
                class_group=class_group,
                student=student,
                is_active=True,
            )
            enrollments += 1
        return created_accounts, enrollments


@transaction.atomic
def transfer_course_enrollment(enrollment, target_class):
    with lock_active_class_groups(
        [target_class],
        inactive_error="目标班级已停用，不能转入学生。",
        lock_courses=True,
    ) as locked_classes:
        enrollment = CourseEnrollment.objects.select_for_update().get(pk=enrollment.pk)
        target_class = locked_classes[target_class.pk]
        if target_class.course_id != enrollment.course_id:
            raise ValidationError("班级必须属于当前课程")
        enrollment.class_group = target_class
        enrollment.full_clean()
        enrollment.save(update_fields=["class_group"])
        return enrollment


@transaction.atomic
def remove_course_enrollment(*, course, class_group, enrollment):
    """Remove one course membership without deleting global or published data."""
    from apps.experiments.models import (
        Experiment,
        ExperimentParticipantOverride,
        ExperimentStudent,
    )

    course = lock_writable_course(course)
    draft_experiments = list(
        Experiment.objects.select_for_update()
        .filter(course_id=course.pk, status=Experiment.Status.DRAFT)
        .order_by("pk")
    )
    try:
        class_group = ClassGroup.objects.select_for_update().get(
            pk=class_group.pk,
            course_id=course.pk,
        )
        enrollment = CourseEnrollment.objects.select_for_update().get(
            pk=enrollment.pk,
            course_id=course.pk,
            class_group_id=class_group.pk,
            student_id=enrollment.student_id,
            is_active=True,
        )
    except (ClassGroup.DoesNotExist, CourseEnrollment.DoesNotExist) as exc:
        raise ValidationError("选课关系已被其他操作更新，请刷新后重试。") from exc

    draft_experiment_ids = [experiment.pk for experiment in draft_experiments]
    draft_student_links_removed, _ = ExperimentStudent.objects.filter(
        experiment_id__in=draft_experiment_ids,
        student_id=enrollment.student_id,
    ).delete()
    draft_overrides_removed, _ = ExperimentParticipantOverride.objects.filter(
        experiment_id__in=draft_experiment_ids,
        student_id=enrollment.student_id,
    ).delete()
    details = {
        "course_id": course.pk,
        "class_id": class_group.pk,
        "student_id": enrollment.student_id,
        "enrollment_id": enrollment.pk,
        "draft_experiment_ids": draft_experiment_ids,
        "draft_student_links_removed": draft_student_links_removed,
        "draft_overrides_removed": draft_overrides_removed,
    }
    enrollment.delete()
    return details


@transaction.atomic
def import_legacy_students(parsed_students, accessible_classes):
    parsed_students = list(parsed_students)
    accessible_classes = {class_group.pk: class_group for class_group in accessible_classes}
    numbers = [item.student_number for item in parsed_students]
    if not parsed_students:
        raise StudentImportError("导入预览已失效，请重新上传文件")
    if User.objects.filter(Q(student_number__in=numbers) | Q(username__in=numbers)).exists():
        raise StudentImportError("部分学号已存在，请重新校验名单")

    target_classes = {}
    for item in parsed_students:
        class_group = accessible_classes.get(item.class_id)
        if class_group is None or str(class_group) != item.class_name:
            raise StudentImportError(f"班级已变更或无权访问：{item.class_name}")
        target_classes[class_group.pk] = class_group

    with lock_active_class_groups(
        target_classes.values(),
        inactive_error=lambda class_group: StudentImportError(
            f"班级已停用，不能导入学生：{class_group}"
        ),
        lock_courses=True,
    ) as locked_classes:
        for item in parsed_students:
            class_group = locked_classes[item.class_id]
            if str(class_group) != item.class_name:
                raise StudentImportError(f"班级已变更或无权访问：{item.class_name}")
            User.objects.create_user(
                username=item.student_number,
                student_number=item.student_number,
                display_name=item.display_name,
                class_group=class_group,
                role=User.Role.STUDENT,
                password=f"S{item.student_number}",
                must_change_password=True,
            )
