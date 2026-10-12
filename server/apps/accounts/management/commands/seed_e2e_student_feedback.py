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
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionOption
from apps.question_bank.services.question_kc_mapping import replace_question_kc_mapping


class Command(BaseCommand):
    help = "Create isolated full-feedback data for the student Playwright flow."

    @transaction.atomic
    def handle(self, *args, **options):
        course = Course.objects.get(code="KT101", term="2026春")
        teacher = User.objects.get(username="teacher")
        class_group = ClassGroup.objects.get(course=course, name="认知追踪实验班")
        students = list(User.objects.filter(username__in=("S001", "S002", "S003")))

        if Experiment.objects.filter(
            course=course,
            name="完整反馈视觉验证",
        ).exists():
            self.stdout.write("The isolated student feedback fixture already exists.")
            return

        bank = QuestionBank.objects.create(
            course=course,
            name="E2E 完整反馈题库",
            owner=teacher,
            created_by=teacher,
            description="Isolated Playwright feedback fixture.",
        )
        question = Question.objects.create(
            bank=bank,
            sequence=101,
            question_type=Question.Type.SINGLE,
            text="12 + 3 等于多少？",
            correct_answer="C",
            explanation="12 与 3 相加得到 15，因此正确选项为 C。",
            teacher_difficulty="0.100",
        )
        for display_order, (key, text) in enumerate(
            (("A", "13"), ("B", "14"), ("C", "15"), ("D", "16")),
            start=1,
        ):
            QuestionOption.objects.create(
                question=question,
                key=key,
                text=text,
                display_order=display_order,
            )

        root = Concept.objects.get(course=course, code="KC1")
        branch = Concept.objects.get(course=course, code="KC2")
        foundation = Concept.objects.get(course=course, code="KC6")
        addition = Concept.objects.get(course=course, code="KC7")
        replace_question_kc_mapping(
            question,
            root_kc_code=root.code,
            nodes=[
                {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                {"kc_code": branch.code, "parent_kc_code": root.code, "order": 1},
                {"kc_code": foundation.code, "parent_kc_code": branch.code, "order": 1},
                {"kc_code": addition.code, "parent_kc_code": branch.code, "order": 2},
            ],
        )

        experiment = Experiment.objects.create(
            course=course,
            name="完整反馈视觉验证",
            created_by=teacher,
            owner=teacher,
            question_bank=bank,
            description="One-question full-feedback Playwright fixture.",
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
        ExperimentSamplingKC.objects.create(experiment=experiment, concept=root)
        assign_students(experiment, students)

        plan = get_or_create_publish_plan(experiment, teacher)
        plan = save_plan_selection(
            plan,
            teacher,
            expected_revision=plan.plan_revision,
            class_ids=[class_group.pk],
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
        generate_preview(plan, teacher, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)
        self.stdout.write(self.style.SUCCESS("Created isolated student feedback fixture."))
