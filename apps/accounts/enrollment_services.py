from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q

from .models import CourseEnrollment, User


@transaction.atomic
def enroll_single_student(course, class_group, student_number, display_name):
    student_number = student_number.strip()
    display_name = display_name.strip()
    if class_group.course_id != course.pk:
        raise ValidationError("班级必须属于当前课程")
    matches = list(
        User.objects.filter(
            Q(student_number=student_number) | Q(username=student_number)
        ).distinct()
    )
    if len(matches) > 1:
        raise ValidationError(f"学号与多个账号冲突：{student_number}")
    created = not matches
    if matches:
        student = matches[0]
        if student.role != User.Role.STUDENT:
            raise ValidationError(f"学号与非学生账号冲突：{student_number}")
        if student.shown_name != display_name:
            raise ValidationError(f"学号对应姓名不一致：{student_number}")
    else:
        student = User.objects.create_user(
            username=student_number,
            student_number=student_number,
            display_name=display_name,
            role=User.Role.STUDENT,
            password=f"S{student_number}",
        )
    if CourseEnrollment.objects.filter(course=course, student=student).exists():
        raise ValidationError(f"学生已加入当前课程：{student_number}")
    CourseEnrollment.objects.create(
        course=course,
        class_group=class_group,
        student=student,
        is_active=True,
    )
    return student, created


@transaction.atomic
def copy_class_enrollments(*, target_course, target_class, source_class):
    if target_class.course_id != target_course.pk:
        raise ValidationError("目标班级必须属于目标课程")
    if source_class.course_id == target_course.pk:
        raise ValidationError("来源班级必须属于其他课程")
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
