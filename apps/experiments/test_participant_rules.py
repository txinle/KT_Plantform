from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.migrations.executor import MigrationExecutor
from django.test import TestCase, TransactionTestCase

from apps.accounts.models import ClassGroup, Course, CourseEnrollment, User
from apps.question_bank.models import QuestionBank

from .models import (
    Experiment,
    ExperimentParticipantClass,
    ExperimentParticipantOverride,
)
from .services import save_participant_rules


class ParticipantRuleModelTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-rule-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="参与规则课程",
            code="PARTICIPANT-RULES",
            term="2026 秋",
        )
        self.class_group = ClassGroup.objects.create(course=self.course, name="规则班")
        self.student = User.objects.create_user(
            username="PARTICIPANT-RULE-STUDENT",
            student_number="PARTICIPANT-RULE-STUDENT",
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=self.course,
            class_group=self.class_group,
            student=self.student,
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="参与规则题库",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="参与规则实验",
        )

    def test_participant_rule_rows_are_unique_per_experiment_target(self):
        ExperimentParticipantClass.objects.create(
            experiment=self.experiment,
            class_group=self.class_group,
        )
        ExperimentParticipantOverride.objects.create(
            experiment=self.experiment,
            student=self.student,
            mode=ExperimentParticipantOverride.Mode.INCLUDE,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExperimentParticipantClass.objects.create(
                    experiment=self.experiment,
                    class_group=self.class_group,
                )
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                ExperimentParticipantOverride.objects.create(
                    experiment=self.experiment,
                    student=self.student,
                    mode=ExperimentParticipantOverride.Mode.EXCLUDE,
                )


class ParticipantRuleMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("experiments", "0004_experiment_selection_mode_and_more")
    migrate_to = ("experiments", "0005_experiment_participant_rules")

    @staticmethod
    def targets_with_experiments_at(executor, experiments_target):
        return [
            experiments_target if target[0] == "experiments" else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        ClassGroup = old_apps.get_model("accounts", "ClassGroup")
        CourseEnrollment = old_apps.get_model("accounts", "CourseEnrollment")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Experiment = old_apps.get_model("experiments", "Experiment")
        ExperimentStudent = old_apps.get_model("experiments", "ExperimentStudent")

        teacher = User.objects.create(username="participant-migration-teacher", role="teacher")
        course = Course.objects.create(
            owner_id=teacher.pk,
            name="历史参与规则课程",
            code="HIST-PARTICIPANT",
            term="2026 秋",
        )
        class_group = ClassGroup.objects.create(course_id=course.pk, name="历史班")
        student = User.objects.create(
            username="PARTICIPANT-MIGRATION-STUDENT",
            student_number="PARTICIPANT-MIGRATION-STUDENT",
            role="student",
        )
        CourseEnrollment.objects.create(
            course_id=course.pk,
            class_group_id=class_group.pk,
            student_id=student.pk,
            is_active=True,
        )
        bank = QuestionBank.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            name="历史参与规则题库",
        )
        draft = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史草稿",
            status="draft",
        )
        published = Experiment.objects.create(
            owner_id=teacher.pk,
            created_by_id=teacher.pk,
            course_id=course.pk,
            question_bank_id=bank.pk,
            name="历史已发布",
            status="published",
        )
        ExperimentStudent.objects.create(experiment_id=draft.pk, student_id=student.pk)
        ExperimentStudent.objects.create(experiment_id=published.pk, student_id=student.pk)
        cls.draft_pk = draft.pk
        cls.published_pk = published.pk
        cls.student_pk = student.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_only_legacy_draft_roster_becomes_explicit_include_rules(self):
        ExperimentStudent = self.apps.get_model("experiments", "ExperimentStudent")
        ExperimentParticipantOverride = self.apps.get_model(
            "experiments",
            "ExperimentParticipantOverride",
        )

        self.assertEqual(
            list(
                ExperimentParticipantOverride.objects.filter(
                    experiment_id=self.draft_pk
                ).values_list("student_id", "mode")
            ),
            [(self.student_pk, "include")],
        )
        self.assertFalse(
            ExperimentParticipantOverride.objects.filter(
                experiment_id=self.published_pk
            ).exists()
        )
        self.assertEqual(
            ExperimentStudent.objects.filter(experiment_id=self.draft_pk).count(),
            1,
        )
        self.assertEqual(
            ExperimentStudent.objects.filter(experiment_id=self.published_pk).count(),
            1,
        )


class ParticipantRuleSaveServiceTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="participant-save-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="规则保存课程",
            code="SAVE-PARTICIPANTS",
            term="2026 秋",
        )
        self.class_a = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="甲班",
        )
        self.class_b = ClassGroup.objects.create(
            course=self.course,
            academic_year="2026",
            name="乙班",
        )
        self.other_course = Course.objects.create(
            owner=self.teacher,
            name="其他课程",
            code="OTHER-PARTICIPANTS",
            term="2026 秋",
        )
        self.other_class = ClassGroup.objects.create(course=self.other_course, name="外班")
        self.student_a1 = self.create_student("SAVE-A1", self.class_a)
        self.student_a2 = self.create_student("SAVE-A2", self.class_a)
        self.student_b1 = self.create_student("SAVE-B1", self.class_b)
        self.student_other = self.create_student(
            "SAVE-OTHER",
            self.other_class,
            course=self.other_course,
        )
        bank = QuestionBank.objects.create(
            owner=self.teacher,
            course=self.course,
            name="规则保存题库",
        )
        self.experiment = Experiment.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            course=self.course,
            question_bank=bank,
            name="规则保存实验",
        )

    def create_student(self, number, class_group, *, course=None):
        student = User.objects.create_user(
            username=number,
            student_number=number,
            display_name=number,
            role=User.Role.STUDENT,
        )
        CourseEnrollment.objects.create(
            course=course or self.course,
            class_group=class_group,
            student=student,
        )
        return student

    def test_class_rule_with_include_and_exclude_materializes_current_preview(self):
        save_participant_rules(
            self.experiment,
            [self.class_a],
            [self.student_b1],
            [self.student_a2],
        )

        self.assertEqual(
            list(
                self.experiment.participant_class_links.values_list("class_group_id", flat=True)
            ),
            [self.class_a.pk],
        )
        self.assertEqual(
            set(
                self.experiment.participant_override_links.values_list(
                    "student_id",
                    "mode",
                )
            ),
            {
                (self.student_b1.pk, ExperimentParticipantOverride.Mode.INCLUDE),
                (self.student_a2.pk, ExperimentParticipantOverride.Mode.EXCLUDE),
            },
        )
        previews = {
            link.student_id: link.class_snapshot_name
            for link in self.experiment.student_links.all()
        }
        self.assertEqual(
            previews,
            {
                self.student_a1.pk: str(self.class_a),
                self.student_b1.pk: str(self.class_b),
            },
        )

    def test_resaving_class_rule_rebuilds_preview_from_latest_enrollment(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        late_student = self.create_student("SAVE-A3", self.class_a)

        save_participant_rules(self.experiment, [self.class_a], [], [])

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            {self.student_a1.pk, self.student_a2.pk, late_student.pk},
        )

    def test_invalid_rules_do_not_replace_existing_preview_or_rules(self):
        save_participant_rules(self.experiment, [self.class_a], [], [])
        expected_preview = set(
            self.experiment.student_links.values_list("student_id", flat=True)
        )
        expected_classes = set(
            self.experiment.participant_class_links.values_list("class_group_id", flat=True)
        )

        with self.assertRaisesMessage(ValidationError, "不能同时"):
            save_participant_rules(
                self.experiment,
                [self.class_a],
                [self.student_b1],
                [self.student_b1],
            )

        self.assertEqual(
            set(self.experiment.student_links.values_list("student_id", flat=True)),
            expected_preview,
        )
        self.assertEqual(
            set(
                self.experiment.participant_class_links.values_list("class_group_id", flat=True)
            ),
            expected_classes,
        )

    def test_rejects_other_course_inactive_or_retired_requested_participants(self):
        with self.assertRaisesMessage(ValidationError, "实验所属课程"):
            save_participant_rules(self.experiment, [self.other_class], [], [])

        self.class_a.is_active = False
        self.class_a.save(update_fields=["is_active"])
        with self.assertRaisesMessage(ValidationError, "停用"):
            save_participant_rules(self.experiment, [self.class_a], [], [])

        CourseEnrollment.objects.filter(
            course=self.course,
            student=self.student_b1,
        ).update(is_active=False)
        with self.assertRaisesMessage(ValidationError, "在读学生"):
            save_participant_rules(self.experiment, [], [self.student_b1], [])
