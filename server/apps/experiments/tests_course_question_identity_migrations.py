from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class CourseQuestionIdentitySnapshotMigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("experiments", "0002_add_course_scope")
    migrate_to = ("experiments", "0003_snapshot_course_question_identity")

    @staticmethod
    def targets_with_experiments_at(executor, experiments_target):
        return [
            experiments_target
            if target[0] == "experiments"
            else ("assessment", "0002_experimentsession_sampling_seeds")
            if target[0] == "assessment"
            else ("analytics", "0013_researchsessionmaterialization_source_revision")
            if target[0] == "analytics"
            else target
            for target in executor.loader.graph.leaf_nodes()
        ]

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        # Class cleanup also runs if historical fixture creation fails in setUpClass.
        cls.addClassCleanup(cls._restore_schema)
        cls.old_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_from)
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Question = old_apps.get_model("question_bank", "Question")
        Experiment = old_apps.get_model("experiments", "Experiment")
        ExperimentVersion = old_apps.get_model("experiments", "ExperimentVersion")
        QuestionSnapshot = old_apps.get_model("experiments", "QuestionSnapshot")

        teacher = User.objects.create(username="snapshot-migration-teacher", role="teacher")
        course = Course.objects.create(
            owner_id=teacher.pk,
            name="旧课程",
            code="LEGACY101",
            term="2026 秋",
        )
        bank = QuestionBank.objects.create(
            course_id=course.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            name="旧题库",
        )
        question = Question.objects.create(
            bank_id=bank.pk,
            sequence=1,
            question_type="short_answer",
            text="旧题目",
            correct_answer="答案",
        )
        experiment = Experiment.objects.create(
            course_id=course.pk,
            created_by_id=teacher.pk,
            owner_id=teacher.pk,
            question_bank_id=bank.pk,
            name="已发布旧实验",
            status="published",
        )
        version = ExperimentVersion.objects.create(
            experiment_id=experiment.pk,
            version_number=1,
            published_by_id=teacher.pk,
        )
        snapshot = QuestionSnapshot.objects.create(
            version_id=version.pk,
            source_question_id=question.pk,
            source_sequence=1,
            code="Q0001",
            question_type="short_answer",
            text="旧题目",
            correct_answer="答案",
            primary_kc="旧 KC",
            all_kcs=["旧 KC"],
        )
        cls.snapshot_pk = snapshot.pk

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls.targets_with_experiments_at(cls.executor, cls.migrate_to)
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def _restore_schema(cls):
        MigrationExecutor(connection).migrate(cls.current_targets)

    def test_existing_published_snapshot_keeps_empty_nullable_identity_fields(self):
        QuestionSnapshot = self.apps.get_model("experiments", "QuestionSnapshot")

        snapshot = QuestionSnapshot.objects.get(pk=self.snapshot_pk)

        self.assertIsNone(snapshot.course_sequence)
        self.assertEqual(snapshot.stable_code, "")
        self.assertIsNone(snapshot.source_bank_id)
        self.assertEqual(snapshot.source_bank_name, "")
        self.assertIsNone(snapshot.bank_sequence)
