from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import ClassGroup, Course, User
from apps.experiments.models import (
    Experiment,
    ExperimentQuestion,
    ExperimentSamplingKC,
)
from apps.experiments.publication_services import (
    commit_publish_plan,
    generate_preview,
    get_or_create_publish_plan,
    save_plan_schedules,
    save_plan_selection,
)
from apps.experiments.services import assign_students
from apps.question_bank.models import Concept, Question, QuestionBank
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping


class Command(BaseCommand):
    help = "Create an isolated manual short-answer grading Playwright fixture."

    @transaction.atomic
    def handle(self, *args, **options):
        course = Course.objects.get(code="KT101", term="2026春")
        teacher = User.objects.get(username="teacher")
        student = User.objects.get(username="S003")
        class_group = ClassGroup.objects.get(course=course, name="认知追踪实验班")
        experiment_name = "人工简答题批改验收"

        if Experiment.objects.filter(course=course, name=experiment_name).exists():
            self.stdout.write("The manual short-answer grading fixture already exists.")
            return

        bank = QuestionBank.objects.create(
            course=course,
            name="E2E 人工简答题题库",
            owner=teacher,
            created_by=teacher,
            description="Isolated Playwright manual-grading fixture.",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=201,
            question_type=Question.Type.SHORT,
            text="请简要说明知识追踪模型要估计的核心状态。",
            correct_answer="模型估计学生随时间变化的知识掌握状态。",
            explanation="答案应说明被估计的是学生的潜在知识状态及其时间变化。",
            teacher_difficulty="0.300",
        )
        concept = Concept.objects.get(course=course, code="KC1")
        replace_question_kc_mapping(
            question,
            root_kc_code=concept.code,
            nodes=[{"kc_code": concept.code, "parent_kc_code": None, "order": 1}],
        )

        experiment = Experiment.objects.create(
            course=course,
            name=experiment_name,
            created_by=teacher,
            owner=teacher,
            question_bank=bank,
            description="One-question manual short-answer grading fixture.",
            sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
            sampling_total_count=1,
            feedback_mode=Experiment.Feedback.FULL,
            resume_mode=Experiment.ResumeMode.RESUMABLE,
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=question,
            display_order=1,
        )
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=concept)
        assign_students(experiment, [student])

        plan = get_or_create_publish_plan(experiment, teacher)
        plan = save_plan_selection(
            plan,
            teacher,
            expected_revision=plan.plan_revision,
            class_ids=[class_group.pk],
            exclusions_by_class={
                class_group.pk: [
                    item.pk
                    for item in class_group.students.exclude(pk=student.pk)
                ]
            },
        )
        plan = save_plan_schedules(
            plan,
            teacher,
            expected_revision=plan.plan_revision,
            default_opens_at=None,
            default_closes_at=None,
            overrides={},
        )
        generate_preview(plan, teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)
        self.stdout.write(self.style.SUCCESS("Created manual short-answer grading fixture."))
