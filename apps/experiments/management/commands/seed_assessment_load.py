import json
import uuid

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import (
    ClassGroup,
    Course,
    CourseEnrollment,
    CourseEnrollmentAccessPeriod,
    CourseSubject,
    ResearchConsent,
    User,
)
from apps.accounts.research_consent import CURRENT_RESEARCH_CONSENT_VERSION
from apps.question_bank.models import (
    Concept,
    Question,
    QuestionBank,
    QuestionConcept,
    QuestionOption,
)
from apps.question_bank.services.course_question_identity import allocate_course_question_identity

from ...models import Experiment, ExperimentQuestion, ExperimentSamplingKC, PublishPlanClass
from ...publication_services import commit_publish_plan, generate_preview, get_or_create_publish_plan


class Command(BaseCommand):
    help = "Create an isolated 300-student, 20-item HTTP load-test fixture in kt_e2e."

    def add_arguments(self, parser):
        parser.add_argument("--run-id", default="")
        parser.add_argument("--students", type=int, default=300)
        parser.add_argument("--items", type=int, default=20)
        parser.add_argument("--password", default="Load300!pass")

    @transaction.atomic
    def handle(self, *args, **options):
        if connection.settings_dict["NAME"] != "kt_e2e":
            raise CommandError("该命令只允许在隔离数据库 kt_e2e 中运行。")
        run_id = options["run_id"].strip() or uuid.uuid4().hex[:10]
        if Course.objects.filter(code=f"LOAD-{run_id}").exists():
            raise CommandError("run-id 已存在，请使用新的 run-id。")
        password = options["password"]
        teacher = User.objects.create_user(
            username=f"load-teacher-{run_id}",
            password=password,
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            owner=teacher,
            name=f"300 人压测 {run_id}",
            code=f"LOAD-{run_id}",
            term="capacity",
        )
        class_group = ClassGroup.objects.create(
            course=course,
            name="并发压测班",
            academic_year="capacity",
        )
        students = []
        for index in range(options["students"]):
            number = f"LOAD-{run_id}-{index + 1:03d}"
            students.append(
                User.objects.create_user(
                    username=number,
                    student_number=number,
                    display_name=f"压测学生{index + 1:03d}",
                    password=password,
                    role=User.Role.STUDENT,
                )
            )
        enrollments = CourseEnrollment.objects.bulk_create(
            [
                CourseEnrollment(
                    course=course, class_group=class_group, student=student
                )
                for student in students
            ],
            batch_size=500,
        )
        CourseEnrollmentAccessPeriod.objects.bulk_create(
            [
                CourseEnrollmentAccessPeriod(
                    enrollment=enrollment,
                    course=course,
                    class_group=class_group,
                    student=enrollment.student,
                    revision=1,
                    active_from=enrollment.enrolled_at,
                )
                for enrollment in enrollments
            ],
            batch_size=500,
        )
        CourseSubject.objects.bulk_create(
            [CourseSubject(course=course, student=student) for student in students],
            batch_size=500,
        )
        ResearchConsent.objects.bulk_create(
            [
                ResearchConsent(
                    student=student, version=CURRENT_RESEARCH_CONSENT_VERSION
                )
                for student in students
            ],
            batch_size=500,
        )
        bank = QuestionBank.objects.create(
            course=course,
            owner=teacher,
            created_by=teacher,
            name=f"压测题库 {run_id}",
        )
        concept = Concept.objects.create(course=course, name="压测知识点")
        questions = []
        for index in range(options["items"]):
            question = Question.objects.create(
                bank=bank,
                sequence=index + 1,
                code=f"LOAD-Q{index + 1:02d}",
                question_type=Question.Type.SINGLE,
                text=f"压测题目 {index + 1}",
                correct_answer="A",
            )
            allocate_course_question_identity(course, question)
            QuestionConcept.objects.create(
                question=question, concept=concept, is_primary=True
            )
            questions.append(question)
        QuestionOption.objects.bulk_create(
            [
                QuestionOption(
                    question=question,
                    key=key,
                    text=key,
                    display_order=order,
                )
                for question in questions
                for order, key in enumerate(("A", "B"), start=1)
            ]
        )
        experiment = Experiment.objects.create(
            course=course,
            owner=teacher,
            created_by=teacher,
            question_bank=bank,
            name=f"300 人并发实验 {run_id}",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=options["items"],
        )
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=concept)
        ExperimentQuestion.objects.bulk_create(
            [
                ExperimentQuestion(
                    experiment=experiment, question=question, display_order=index
                )
                for index, question in enumerate(questions, start=1)
            ]
        )
        plan = get_or_create_publish_plan(experiment, teacher)
        PublishPlanClass.objects.create(publish_plan=plan, class_group=class_group)
        preview = generate_preview(plan, teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(
            plan, teacher, expected_revision=plan.plan_revision
        )[0]
        manifest = {
            "run_id": run_id,
            "task_id": task.pk,
            "student_count": len(students),
            "item_count": len(questions),
            "password": password,
            "usernames": [student.username for student in students],
            "preview_run_id": preview.pk,
        }
        self.stdout.write(json.dumps(manifest, ensure_ascii=False, indent=2))
