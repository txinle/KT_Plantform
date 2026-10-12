from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class RedactQuestionBankVisibilityAuditMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("audit", "0001_initial")
    migrate_to = ("audit", "0002_redact_question_bank_visibility_audits")

    @staticmethod
    def targets_with_audit_at(executor, audit_target):
        return [
            audit_target if target[0] == "audit" else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls.targets_with_audit_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        AuditLog = old_apps.get_model("audit", "AuditLog")
        teacher = User.objects.create(username="migration-owner", role="teacher")
        course = Course.objects.create(
            name="迁移课程",
            code="MIG101",
            term="2026 秋",
            owner_id=teacher.pk,
        )
        bank = QuestionBank.objects.create(
            course_id=course.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="不应保留的题库名称",
        )
        AuditLog.objects.create(
            action="question_bank.visibility",
            object_type="QuestionBank",
            object_id=str(bank.pk),
            details={"visibility": "shared", "name": "不应保留的题库名称"},
        )
        cls.bank_pk = bank.pk
        cls.course_pk = course.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls.targets_with_audit_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_legacy_visibility_audit_values_are_replaced_by_identifiers_and_changed_fields(self):
        AuditLog = self.apps.get_model("audit", "AuditLog")
        audit = AuditLog.objects.get(action="question_bank.visibility")

        self.assertEqual(
            audit.details,
            {
                "bank_id": self.bank_pk,
                "course_id": self.course_pk,
                "changed_fields": ["visibility"],
            },
        )
        self.assertNotIn("shared", str(audit.details))
        self.assertNotIn("不应保留的题库名称", str(audit.details))
