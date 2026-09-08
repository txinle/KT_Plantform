import csv
import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.experiments.models import (
    ClassExperimentTask,
    Experiment,
    ExperimentQuestion,
    ExperimentQuestionQuota,
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


COURSE_CODE = "KT101"
COURSE_TERM = "E2E-CAPTURE-V2"
CLASS_NAME = "默认班"
EXPERIMENT_NAME = "E2E Capture V2 五题回放"
BANK_NAME = "E2E Capture V2 193 子集"

SOURCE_TYPES = (
    ("单选题", Question.Type.SINGLE),
    ("判断题", Question.Type.TRUE_FALSE),
    ("多选题", Question.Type.MULTIPLE),
    ("填空题", Question.Type.FILL_BLANK),
    ("简答题", Question.Type.SHORT),
)


class Command(BaseCommand):
    help = "Create the isolated five-item capture-v2 browser fixture in kt_e2e."
    course_code = COURSE_CODE
    course_term = COURSE_TERM
    class_name = CLASS_NAME
    experiment_name = EXPERIMENT_NAME
    bank_name = BANK_NAME
    source_types = SOURCE_TYPES
    fixture_source_types = SOURCE_TYPES
    leaf_codes = ("KC2", "KC2", "KC3", "KC4", "KC5")
    course_name = "KT101 Capture V2 E2E"
    course_description = "Isolated browser fixture for replaying five response types."
    bank_description = "First item of each type from eda_chapter1_193.csv."
    experiment_description = "One item each for single, true/false, multiple, fill blank, and short answer."
    confidence_mode = Experiment.ConfidenceMode.OFF

    def handle(self, *args, **options):
        if str(connection.settings_dict["NAME"]) != "kt_e2e":
            raise CommandError("seed_e2e_capture_v2 only runs against kt_e2e.")

        with transaction.atomic():
            payload = self._seed()
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _seed(self):
        teacher = self._teacher()
        course, _ = Course.objects.get_or_create(
            owner=teacher,
            code=self.course_code,
            term=self.course_term,
            defaults={
                "name": self.course_name,
                "description": self.course_description,
            },
        )
        class_group, _ = ClassGroup.objects.get_or_create(
            course=course,
            name=self.class_name,
            academic_year="2026",
            defaults={"owner": teacher},
        )
        student = self._student(class_group)
        CourseEnrollment.objects.update_or_create(
            course=course,
            student=student,
            defaults={"class_group": class_group, "is_active": True},
        )

        experiment = Experiment.objects.filter(
            course=course,
            name=self.experiment_name,
        ).first()
        if experiment is not None:
            task = (
                ClassExperimentTask.objects.select_related("release_snapshot")
                .filter(experiment=experiment, class_group=class_group)
                .first()
            )
            if task is not None:
                return self._payload(task, student)
            if experiment.status != Experiment.Status.DRAFT:
                raise CommandError(
                    "The capture-v2 E2E experiment is frozen without its default task."
                )

        bank, _ = QuestionBank.objects.get_or_create(
            course=course,
            name=self.bank_name,
            defaults={
                "owner": teacher,
                "created_by": teacher,
                "description": self.bank_description,
            },
        )
        root, leaves = self._concepts(course)
        questions = self._questions(bank, root, leaves)

        if experiment is None:
            experiment = Experiment.objects.create(
                course=course,
                name=self.experiment_name,
                created_by=teacher,
                owner=teacher,
                question_bank=bank,
                description=self.experiment_description,
                selection_mode=Experiment.SelectionMode.ALL,
                sampling_mode=Experiment.SamplingMode.TOTAL_COUNT,
                sampling_total_count=len(questions),
                sampling_match_scope=Experiment.MatchScope.PRIMARY,
                ordering_mode=Experiment.Ordering.FIXED,
                feedback_mode=Experiment.Feedback.FULL,
                feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
                resume_mode=Experiment.ResumeMode.RESUMABLE,
                confidence_mode=self.confidence_mode,
                research_tools=self._research_tools(questions),
            )

        for display_order, question in enumerate(questions, start=1):
            ExperimentQuestion.objects.get_or_create(
                experiment=experiment,
                question=question,
                defaults={"display_order": display_order},
            )
            ExperimentQuestionQuota.objects.get_or_create(
                experiment=experiment,
                question_type=question.question_type,
                defaults={"amount": 1},
            )
        ExperimentSamplingKC.objects.get_or_create(
            experiment=experiment,
            concept=root,
            defaults={"match_scope": Experiment.MatchScope.PRIMARY},
        )
        assign_students(experiment, [student])

        excluded_student_ids = list(
            CourseEnrollment.objects.filter(
                course=course,
                class_group=class_group,
                is_active=True,
            )
            .exclude(student=student)
            .values_list("student_id", flat=True)
        )
        plan = get_or_create_publish_plan(experiment, teacher)
        plan = save_plan_selection(
            plan,
            teacher,
            expected_revision=plan.plan_revision,
            class_ids=[class_group.pk],
            exclusions_by_class={class_group.pk: excluded_student_ids},
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
        tasks = commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)
        task = next(task for task in tasks if task.class_group_id == class_group.pk)
        return self._payload(task, student)

    @staticmethod
    def _teacher():
        teacher, _ = User.objects.get_or_create(
            username="teacher",
            defaults={
                "role": User.Role.TEACHER,
                "display_name": "E2E 教师",
                "is_active": True,
            },
        )
        teacher.role = User.Role.TEACHER
        teacher.display_name = teacher.display_name or "E2E 教师"
        teacher.is_active = True
        teacher.set_password("teacher123")
        teacher.save()
        return teacher

    @staticmethod
    def _student(class_group):
        student, _ = User.objects.get_or_create(
            username="S002",
            defaults={
                "role": User.Role.STUDENT,
                "student_number": "S002",
                "display_name": "学生2",
                "class_group": class_group,
                "is_active": True,
            },
        )
        student.role = User.Role.STUDENT
        student.student_number = "S002"
        student.display_name = student.display_name or "学生2"
        student.class_group = class_group
        student.is_active = True
        student.set_password("student123")
        student.uses_initial_password = False
        student.initial_password_reminder_shown_at = None
        student.initial_password_state_recorded_at = timezone.now()
        student.must_change_password = False
        student.save()
        return student

    @staticmethod
    def _concepts(course):
        root, _ = Concept.objects.get_or_create(
            course=course,
            code="KC1",
            defaults={"name": "EDA 基础"},
        )
        leaves = {}
        for code, name in (
            ("KC2", "EDA 技术的基本概念与作用"),
            ("KC3", "FPGA 开发中的 EDA 应用"),
            ("KC4", "EDA 硬件描述语言"),
            ("KC5", "EDA 综合应用"),
        ):
            leaves[code], _ = Concept.objects.get_or_create(
                course=course,
                code=code,
                defaults={"name": name},
            )
        return root, leaves

    def _questions(self, bank, root, leaves):
        rows = self._source_rows()
        questions = []
        for (source_type, question_type), leaf_code in zip(self.source_types, self.leaf_codes):
            row = rows[source_type]
            sequence = int(row["序号"])
            question, created = Question.objects.get_or_create(
                bank=bank,
                sequence=sequence,
                defaults={
                    "code": f"EDA-{sequence:03d}",
                    "question_type": question_type,
                    "text": row["题目"],
                    "correct_answer": self._correct_answer(question_type, row["答案"]),
                    "accepted_answers": self._accepted_answers(row["可接受答案"]),
                    "explanation": row["解析 "],
                },
            )
            if created:
                for display_order, key in enumerate("ABCDEF", start=1):
                    text = row[f"选项{key}"]
                    if text:
                        QuestionOption.objects.create(
                            question=question,
                            key=key,
                            text=text,
                            display_order=display_order,
                        )
                replace_question_kc_mapping(
                    question,
                    root_kc_code=root.code,
                    nodes=[
                        {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                        {
                            "kc_code": leaves[leaf_code].code,
                            "parent_kc_code": root.code,
                            "order": 1,
                        },
                    ],
                )
            questions.append(question)
        return questions

    @staticmethod
    def _correct_answer(question_type, value):
        if question_type == Question.Type.TRUE_FALSE:
            if value == "正确":
                return True
            if value == "错误":
                return False
            raise CommandError("The 193-item true/false source has an unsupported answer.")
        return value

    @staticmethod
    def _accepted_answers(value):
        return [
            answer.strip()
            for answer in value.replace("；", ";").split(";")
            if answer.strip()
        ]

    def _source_rows(self):
        fixture = (
            Path(__file__).resolve().parents[3]
            / "assessment"
            / "fixtures"
            / "eda_chapter1_193.csv"
        )
        with fixture.open(encoding="utf-8-sig", newline="") as source:
            rows = list(csv.DictReader(source))
        return {
            source_type: next(row for row in rows if row["题型"] == source_type)
            for source_type, _question_type in self.fixture_source_types
        }

    @staticmethod
    def _research_tools(questions):
        codes = {question.question_type: question.code for question in questions}
        single_code = codes[Question.Type.SINGLE]
        fill_code = codes[Question.Type.FILL_BLANK]
        return {
            "doubt_enabled": True,
            "check_enabled": True,
            "kc_visible": True,
            "hints": [
                {
                    "id": "fill-hint-1",
                    "question_code": fill_code,
                    "level": 1,
                    "text": "先回忆 EDA 用什么方式描述电路，而不是逐门手工连接。",
                    "contains_answer": False,
                },
                {
                    "id": "fill-hint-2",
                    "question_code": fill_code,
                    "level": 2,
                    "text": "题干要的是这种描述方式的规范名称或常见缩写。",
                    "contains_answer": False,
                },
            ],
            "rules": [
                {
                    "id": "required-check-single",
                    "tool": "check",
                    "trigger": "item_enter",
                    "question_code": single_code,
                    "after_seconds": 0,
                    "max_exposures": 1,
                    "required": True,
                    "hint_id": "",
                },
                {
                    "id": "optional-fill-hint-1",
                    "tool": "hint",
                    "trigger": "item_enter",
                    "question_code": fill_code,
                    "after_seconds": 0,
                    "max_exposures": 1,
                    "required": False,
                    "hint_id": "fill-hint-1",
                },
                {
                    "id": "optional-fill-hint-2",
                    "tool": "hint",
                    "trigger": "item_enter",
                    "question_code": fill_code,
                    "after_seconds": 0,
                    "max_exposures": 1,
                    "required": False,
                    "hint_id": "fill-hint-2",
                },
            ],
        }

    @staticmethod
    def _payload(task, student):
        snapshots = list(
            task.release_snapshot.question_snapshots.select_related("source_question").order_by(
                "source_sequence", "pk"
            )
        )
        return {
            "task_id": task.pk,
            "student": {
                "id": student.pk,
                "username": student.username,
                "student_number": student.student_number,
            },
            "course": {
                "id": task.experiment.course_id,
                "code": task.experiment.course.code,
                "term": task.experiment.course.term,
            },
            "tasks": [
                {
                    "id": task.pk,
                    "class_group_id": task.class_group_id,
                    "experiment_id": task.experiment_id,
                    "release_snapshot_id": task.release_snapshot_id,
                }
            ],
            "question_refs": [
                {
                    "question_id": snapshot.source_question_id,
                    "snapshot_id": snapshot.pk,
                    "code": snapshot.code,
                    "source_sequence": snapshot.source_sequence,
                    "question_type": snapshot.question_type,
                }
                for snapshot in snapshots
            ],
        }
