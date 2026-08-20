from datetime import datetime

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase
from django.utils import timezone


class CourseQuestionIdentityMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("question_bank", "0002_add_course_scope")
    migrate_to = ("question_bank", "0003_course_question_identity")

    @staticmethod
    def targets_with_question_bank_at(executor, question_bank_target):
        return [
            question_bank_target if target[0] == "question_bank" else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls.targets_with_question_bank_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Question = old_apps.get_model("question_bank", "Question")

        teacher = User.objects.create(username="identity-migration-teacher", role="teacher")
        cls.course_a = Course.objects.create(
            owner_id=teacher.pk,
            name="课程 A",
            code="A101",
            term="2026 秋",
        )
        cls.course_b = Course.objects.create(
            owner_id=teacher.pk,
            name="课程 B",
            code="B101",
            term="2026 秋",
        )
        later_bank = QuestionBank.objects.create(
            course_id=cls.course_a.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="较晚题库",
        )
        earlier_bank = QuestionBank.objects.create(
            course_id=cls.course_a.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="较早题库",
        )
        bank_b = QuestionBank.objects.create(
            course_id=cls.course_b.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="课程 B 题库",
        )
        legacy_bank = QuestionBank.objects.create(
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="遗留题库",
        )
        QuestionBank.objects.filter(pk=earlier_bank.pk).update(
            created_at=timezone.make_aware(datetime(2026, 1, 1, 8, 0, 0))
        )
        QuestionBank.objects.filter(pk=later_bank.pk).update(
            created_at=timezone.make_aware(datetime(2026, 1, 2, 8, 0, 0))
        )

        cls.earlier_question_low = Question.objects.create(
            bank_id=earlier_bank.pk,
            sequence=2,
            question_type="short_answer",
            text="较早题库低序号",
            correct_answer="A",
        )
        cls.earlier_question_high = Question.objects.create(
            bank_id=earlier_bank.pk,
            sequence=8,
            question_type="short_answer",
            text="较早题库高序号",
            correct_answer="B",
        )
        cls.later_question = Question.objects.create(
            bank_id=later_bank.pk,
            sequence=1,
            question_type="short_answer",
            text="较晚题库题目",
            correct_answer="C",
        )
        cls.question_b = Question.objects.create(
            bank_id=bank_b.pk,
            sequence=99,
            question_type="short_answer",
            text="课程 B 题目",
            correct_answer="D",
        )
        cls.legacy_question = Question.objects.create(
            bank_id=legacy_bank.pk,
            sequence=1,
            question_type="short_answer",
            text="遗留题目",
            correct_answer="E",
        )

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls.targets_with_question_bank_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_backfill_is_course_isolated_and_uses_the_documented_deterministic_order(self):
        Identity = self.apps.get_model("question_bank", "CourseQuestionIdentity")

        course_a_rows = list(
            Identity.objects.filter(course_id=self.course_a.pk).order_by("course_sequence")
        )
        self.assertEqual(
            [row.question_id for row in course_a_rows],
            [
                self.earlier_question_low.pk,
                self.earlier_question_high.pk,
                self.later_question.pk,
            ],
        )
        self.assertEqual([row.course_sequence for row in course_a_rows], [1, 2, 3])
        self.assertEqual(
            [row.stable_code for row in course_a_rows],
            [
                f"C{self.course_a.pk:06d}-Q000001",
                f"C{self.course_a.pk:06d}-Q000002",
                f"C{self.course_a.pk:06d}-Q000003",
            ],
        )

        course_b_rows = list(Identity.objects.filter(course_id=self.course_b.pk))
        self.assertEqual(
            [(row.question_id, row.course_sequence, row.stable_code) for row in course_b_rows],
            [(self.question_b.pk, 1, f"C{self.course_b.pk:06d}-Q000001")],
        )
        self.assertFalse(Identity.objects.filter(question_id=self.legacy_question.pk).exists())
