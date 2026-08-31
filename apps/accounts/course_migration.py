from django.db import models


def backfill_legacy_courses(apps, schema_editor):
    User = apps.get_model("accounts", "User")
    Course = apps.get_model("accounts", "Course")
    CourseTeacher = apps.get_model("accounts", "CourseTeacher")
    ClassGroup = apps.get_model("accounts", "ClassGroup")
    CourseEnrollment = apps.get_model("accounts", "CourseEnrollment")
    QuestionBank = apps.get_model("question_bank", "QuestionBank")
    Concept = apps.get_model("question_bank", "Concept")
    QuestionConcept = apps.get_model("question_bank", "QuestionConcept")
    Experiment = apps.get_model("experiments", "Experiment")
    ExperimentStudent = apps.get_model("experiments", "ExperimentStudent")
    ExperimentVersion = apps.get_model("experiments", "ExperimentVersion")

    courses = {}
    resource_owners = User.objects.filter(role__in=["teacher", "admin"]).order_by("pk")
    for teacher in resource_owners:
        course, _ = Course.objects.get_or_create(
            owner_id=teacher.pk,
            code=f"LEGACY-{teacher.pk}",
            term="迁移",
            defaults={
                "name": "历史数据（迁移）",
                "description": "系统升级时自动归集的历史课程数据。",
                "status": "active",
            },
        )
        courses[teacher.pk] = course
        ClassGroup.objects.filter(owner_id=teacher.pk, course_id__isnull=True).update(course_id=course.pk)
        QuestionBank.objects.filter(owner_id=teacher.pk, course_id__isnull=True).update(
            course_id=course.pk,
            created_by_id=teacher.pk,
        )

    for bank in QuestionBank.objects.exclude(course_id__isnull=True).iterator():
        for link in QuestionConcept.objects.filter(question__bank_id=bank.pk).select_related("concept"):
            source = link.concept
            if source.course_id == bank.course_id:
                continue
            target, _ = Concept.objects.get_or_create(
                course_id=bank.course_id,
                normalized_name=source.normalized_name,
                defaults={
                    "name": source.name,
                    "scope": "private",
                    "owner_id": None,
                },
            )
            existing = QuestionConcept.objects.filter(
                question_id=link.question_id,
                concept_id=target.pk,
            ).exclude(pk=link.pk).first()
            if existing is not None:
                keep_primary = existing.is_primary or link.is_primary
                models.QuerySet.delete(
                    QuestionConcept.objects.filter(pk=link.pk)
                )
                if keep_primary and not existing.is_primary:
                    models.QuerySet.update(
                        QuestionConcept.objects.filter(pk=existing.pk),
                        is_primary=True,
                    )
            else:
                models.QuerySet.update(
                    QuestionConcept.objects.filter(pk=link.pk),
                    concept_id=target.pk,
                )

    for experiment in Experiment.objects.select_related("question_bank").iterator():
        if experiment.course_id is None:
            course_id = experiment.question_bank.course_id
            if course_id is None and experiment.owner_id in courses:
                course_id = courses[experiment.owner_id].pk
            experiment.course_id = course_id
        if experiment.created_by_id is None:
            experiment.created_by_id = experiment.owner_id
        experiment.save(update_fields=["course", "created_by"])
        if (
            experiment.owner_id
            and experiment.course_id
            and Course.objects.get(pk=experiment.course_id).owner_id != experiment.owner_id
            and User.objects.filter(pk=experiment.owner_id, role="teacher").exists()
        ):
            CourseTeacher.objects.get_or_create(
                course_id=experiment.course_id,
                teacher_id=experiment.owner_id,
            )

    for student in User.objects.filter(role="student").exclude(class_group_id__isnull=True).select_related("class_group"):
        class_group = student.class_group
        if class_group.course_id:
            CourseEnrollment.objects.get_or_create(
                course_id=class_group.course_id,
                student_id=student.pk,
                defaults={"class_group_id": class_group.pk, "is_active": True},
            )

    unassigned_classes = {}
    for link in ExperimentStudent.objects.select_related("experiment").iterator():
        course_id = link.experiment.course_id
        if not course_id:
            continue
        if CourseEnrollment.objects.filter(course_id=course_id, student_id=link.student_id).exists():
            continue
        class_group = unassigned_classes.get(course_id)
        if class_group is None:
            course = Course.objects.get(pk=course_id)
            class_group, _ = ClassGroup.objects.get_or_create(
                course_id=course_id,
                name="未分班",
                academic_year="",
                defaults={"owner_id": course.owner_id, "is_active": True},
            )
            unassigned_classes[course_id] = class_group
        CourseEnrollment.objects.create(
            course_id=course_id,
            student_id=link.student_id,
            class_group_id=class_group.pk,
            is_active=True,
        )

    for version in ExperimentVersion.objects.select_related("experiment__course").iterator():
        course = version.experiment.course
        if not course:
            continue
        version.course_name_snapshot = course.name
        version.course_code_snapshot = course.code
        version.course_term_snapshot = course.term
        version.save(
            update_fields=[
                "course_name_snapshot",
                "course_code_snapshot",
                "course_term_snapshot",
            ]
        )


def noop_reverse(apps, schema_editor):
    pass
