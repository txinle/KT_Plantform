from django.core.management.base import BaseCommand
from django.db import transaction
from apps.accounts.class_lifecycle import lock_active_class_groups
from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import Experiment, ExperimentQuestion
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_schedules,
    save_plan_selection,
)
from apps.experiments.services import assign_students
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption

class Command(BaseCommand):
    help = "创建可直接登录体验的演示数据"
    @transaction.atomic
    def handle(self, *args, **options):
        admin, _ = User.objects.get_or_create(username="admin", defaults={"role": User.Role.ADMIN, "display_name": "系统管理员", "is_staff": True, "is_superuser": True})
        admin.set_password("admin123"); admin.is_active = True; admin.is_staff = True; admin.is_superuser = True; admin.save()
        teacher, _ = User.objects.get_or_create(username="teacher", defaults={"role": User.Role.TEACHER, "display_name": "演示教师"})
        teacher.set_password("teacher123"); teacher.is_active = True; teacher.save()
        course, _ = Course.objects.get_or_create(
            owner=teacher,
            code="KT101",
            term="2026春",
            defaults={"name": "知识追踪研究方法", "description": "课程中心式演示工作区"},
        )
        cls, _ = ClassGroup.objects.get_or_create(
            course=course,
            name="认知追踪实验班",
            academic_year="2026",
            defaults={"owner": teacher},
        )
        students = []
        for index in range(1, 4):
            number = f"S{index:03d}"
            student, _ = User.objects.get_or_create(username=number, defaults={"role": User.Role.STUDENT, "student_number": number, "display_name": f"学生{index}"})
            student.role = User.Role.STUDENT; student.student_number = number; student.set_password("student123"); student.is_active = True; student.save(); students.append(student)
        bank, created = QuestionBank.objects.get_or_create(
            course=course,
            name="演示题库",
            defaults={"created_by": teacher, "owner": teacher, "description": "用于验证单题导航与交互数据采集"},
        )
        if created or not bank.questions.exists():
            kc_math, _ = Concept.objects.get_or_create(course=course, normalized_name="基础运算", defaults={"name": "基础运算"})
            kc_prime, _ = Concept.objects.get_or_create(course=course, normalized_name="质数", defaults={"name": "质数"})
            data = [
                (1, Question.Type.SINGLE, "2 + 2 等于多少？", {"A":"3","B":"4","C":"5","D":"6"}, "B", kc_math, "0.100"),
                (2, Question.Type.MULTIPLE, "下列哪些数是质数？", {"A":"2","B":"4","C":"5","D":"6"}, ["A","C"], kc_prime, "0.350"),
                (3, Question.Type.NUMERIC, "圆周率保留两位小数约为多少？", {}, "3.14", kc_math, "0.250"),
                (4, Question.Type.SHORT, "英文术语 Knowledge Tracing 的中文名称是什么？", {}, "知识追踪", kc_math, "0.450"),
            ]
            for seq, qtype, text, options, answer, kc, difficulty in data:
                q = Question.objects.create(bank=bank, sequence=seq, question_type=qtype, text=text, correct_answer=answer, teacher_difficulty=difficulty, accepted_answers=["认知追踪"] if seq==4 else [], absolute_tolerance="0.01" if seq==3 else None)
                for order, (key, value) in enumerate(options.items(), start=1): QuestionOption.objects.create(question=q, key=key, text=value, display_order=order)
                QuestionConcept.objects.create(question=q, concept=kc, is_primary=True)
        experiment, created = Experiment.objects.get_or_create(
            course=course,
            name="演示交互数据采集",
            defaults={"created_by": teacher, "owner": teacher, "question_bank": bank, "description": "自由导航、草稿保存、每题提交锁定", "ordering_mode": Experiment.Ordering.STRATIFIED, "feedback_mode": Experiment.Feedback.CORRECTNESS, "resume_mode": Experiment.ResumeMode.RESUMABLE},
        )
        experiment = Experiment.objects.select_for_update().get(pk=experiment.pk)
        with lock_active_class_groups(
            [cls],
            inactive_error="演示班级已停用，不能写入选课。",
            lock_courses=True,
        ) as locked_classes:
            cls = locked_classes[cls.pk]
            for student in students:
                CourseEnrollment.objects.update_or_create(
                    course=course,
                    student=student,
                    defaults={"class_group": cls, "is_active": True},
                )
        if created:
            for idx, q in enumerate(bank.questions.all(), start=1): ExperimentQuestion.objects.create(experiment=experiment, question=q, display_order=idx)
            assign_students(experiment, students)
        elif experiment.status == Experiment.Status.DRAFT and not (
            experiment.participant_class_links.exists()
            or experiment.participant_override_links.exists()
        ):
            assign_students(experiment, students)
        if experiment.status == Experiment.Status.DRAFT and not experiment.versions.exists():
            plan = get_or_create_publish_plan(experiment, teacher)
            plan = save_plan_selection(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
                class_ids=[cls.pk],
                exclusions_by_class={},
            )
            plan = save_plan_schedules(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
                default_opens_at=None,
                default_closes_at=None,
                overrides={},
            )
            generate_preview(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
            )
            plan.refresh_from_db()
            commit_publish_plan(
                plan,
                teacher,
                expected_revision=plan.plan_revision,
            )
        self.stdout.write(self.style.SUCCESS("演示数据已就绪：admin/admin123，teacher/teacher123，S001-S003/student123"))
