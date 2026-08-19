from __future__ import annotations
import hashlib
import random
from collections import defaultdict, deque
from django.core.exceptions import ValidationError
from django.db import transaction
from apps.accounts.class_lifecycle import lock_active_class_groups
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion, QuestionSnapshot


def validate_for_publish(experiment):
    if experiment.question_bank.course_id != experiment.course_id:
        raise ValidationError("实验与题库必须属于同一课程")
    links = list(experiment.question_links.select_related("question").prefetch_related("question__options", "question__concept_links__concept"))
    if not links: raise ValidationError("实验至少需要一道题")
    missing = [link.question.code for link in links if not link.question.primary_concept]
    if missing: raise ValidationError(f"以下题目缺少主知识点：{', '.join(missing)}")
    if not experiment.student_links.exists(): raise ValidationError("实验至少需要分配一名学生")
    return links

@transaction.atomic
def publish_experiment(experiment, actor):
    experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
    if experiment.status != Experiment.Status.DRAFT:
        raise ValidationError("只有草稿实验可以发布")
    links = validate_for_publish(experiment)
    version_number = (experiment.versions.order_by("-version_number").values_list("version_number", flat=True).first() or 0) + 1
    config = {
        "ordering_mode": experiment.ordering_mode, "feedback_mode": experiment.feedback_mode,
        "time_mode": experiment.time_mode, "total_time_limit_minutes": experiment.total_time_limit_minutes,
        "per_question_time_limit_seconds": experiment.per_question_time_limit_seconds,
        "resume_mode": experiment.resume_mode,
    }
    course_snapshot = {
        "course_name_snapshot": experiment.course.name if experiment.course_id else "",
        "course_code_snapshot": experiment.course.code if experiment.course_id else "",
        "course_term_snapshot": experiment.course.term if experiment.course_id else "",
    }
    version = ExperimentVersion.objects.create(
        experiment=experiment,
        version_number=version_number,
        configuration=config,
        published_by=actor,
        **course_snapshot,
    )
    for link in links:
        q = link.question
        kc_links = list(q.concept_links.select_related("concept"))
        primary = next(item.concept.name for item in kc_links if item.is_primary)
        QuestionSnapshot.objects.create(
            version=version, source_question=q, source_sequence=link.display_order, code=q.code,
            question_type=q.question_type, text=q.text,
            options=[{"key": item.key, "text": item.text} for item in q.options.all()],
            correct_answer=q.correct_answer, accepted_answers=q.accepted_answers,
            absolute_tolerance=q.absolute_tolerance, relative_tolerance=q.relative_tolerance,
            explanation=q.explanation, teacher_difficulty=q.teacher_difficulty,
            empirical_difficulty=q.empirical_difficulty, primary_kc=primary,
            all_kcs=[item.concept.name for item in kc_links],
        )
    experiment.status = Experiment.Status.PUBLISHED
    experiment.save(update_fields=["status", "updated_at"])
    return version


def seed_for(version_id, student_id):
    return int(hashlib.sha256(f"{version_id}:{student_id}".encode()).hexdigest()[:16], 16)


def ordered_snapshots(version, student_id):
    items = list(version.question_snapshots.all())
    mode = version.configuration.get("ordering_mode", "fixed")
    rng = random.Random(seed_for(version.id, student_id))
    if mode == "random":
        rng.shuffle(items)
    elif mode == "stratified_random":
        groups = defaultdict(list)
        for item in items:
            difficulty = float(item.teacher_difficulty if item.teacher_difficulty is not None else 0.5)
            band = 0 if difficulty < 1/3 else 1 if difficulty < 2/3 else 2
            groups[(item.primary_kc, band)].append(item)
        queues = []
        for key in sorted(groups):
            rng.shuffle(groups[key]); queues.append(deque(groups[key]))
        rng.shuffle(queues)
        items = []
        while any(queues):
            for queue in queues:
                if queue: items.append(queue.popleft())
    return items

@transaction.atomic
def sync_experiment_questions(experiment):
    if experiment.status != Experiment.Status.DRAFT: raise ValidationError("发布后不能修改题目")
    experiment.question_links.all().delete()
    for idx, question in enumerate(experiment.question_bank.questions.filter(status="active"), start=1):
        ExperimentQuestion.objects.create(experiment=experiment, question=question, display_order=idx)

@transaction.atomic
def assign_students(experiment, students):
    if experiment.status != Experiment.Status.DRAFT: raise ValidationError("发布后不能修改学生")
    students = list(students)
    student_ids = [student.pk for student in students]
    if experiment.course_id is None:
        from apps.accounts.models import User

        student_class_ids = dict(
            User.objects.filter(pk__in=student_ids).values_list("pk", "class_group_id")
        )
        with lock_active_class_groups(
            student_class_ids.values(),
            inactive_error="不能分配停用班级中的学生",
        ) as locked_classes:
            experiment.student_links.all().delete()
            for student in students:
                class_group = locked_classes.get(student_class_ids[student.pk])
                ExperimentStudent.objects.create(
                    experiment=experiment,
                    student=student,
                    class_snapshot_name=str(class_group or ""),
                )
        return

    from apps.accounts.models import CourseEnrollment

    enrollments = {
        item["student_id"]: item
        for item in CourseEnrollment.objects.filter(
            course=experiment.course,
            student_id__in=student_ids,
            is_active=True,
        ).values("student_id", "class_group_id")
    }
    if len(enrollments) != len(students):
        raise ValidationError("只能分配当前课程可用班级中的在读学生")
    with lock_active_class_groups(
        [enrollment["class_group_id"] for enrollment in enrollments.values()],
        inactive_error="只能分配当前课程可用班级中的在读学生",
    ) as locked_classes:
        experiment.student_links.all().delete()
        for student in students:
            enrollment = enrollments[student.pk]
            ExperimentStudent.objects.create(
                experiment=experiment,
                student=student,
                class_snapshot_name=str(locked_classes[enrollment["class_group_id"]]),
            )
