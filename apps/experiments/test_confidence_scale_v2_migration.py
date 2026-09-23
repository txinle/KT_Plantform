from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ConfidenceScaleV2MigrationTests(TransactionTestCase):
    reset_sequences = True
    migrate_from = ("experiments", "0025_allow_unassigned_draft_bank")
    migrate_to = ("experiments", "0026_confidence_scale_v2")

    @staticmethod
    def targets_with_experiments_at(executor, experiments_target):
        return [
            experiments_target if target[0] == "experiments"
            else ("assessment", "0025_experimentsession_research_source_revision")
            if target[0] == "assessment"
            else target
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
        Experiment = old_apps.get_model("experiments", "Experiment")
        ExperimentVersion = old_apps.get_model("experiments", "ExperimentVersion")
        teacher = User.objects.create(username="scale-v2-migration-teacher", role="teacher")
        cls.draft_pk = Experiment.objects.create(
            name="部署时草稿",
            status="draft",
            confidence_scale_version="v1",
        ).pk
        cls.frozen_pk = Experiment.objects.create(
            name="已冻结实验",
            status="frozen",
            confidence_scale_version="v1",
        ).pk
        published = Experiment.objects.create(
            name="已发布实验",
            status="published",
            confidence_scale_version="v1",
        )
        cls.published_pk = published.pk
        cls.version_pk = ExperimentVersion.objects.create(
            experiment_id=published.pk,
            version_number=1,
            published_by_id=teacher.pk,
            configuration={"confidence_scale_version": "v1"},
        ).pk

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

    def test_only_drafts_move_to_v2_and_frozen_configuration_is_untouched(self):
        Experiment = self.apps.get_model("experiments", "Experiment")
        ExperimentVersion = self.apps.get_model("experiments", "ExperimentVersion")

        self.assertEqual(Experiment.objects.get(pk=self.draft_pk).confidence_scale_version, "v2")
        self.assertEqual(Experiment.objects.get(pk=self.frozen_pk).confidence_scale_version, "v1")
        self.assertEqual(Experiment.objects.get(pk=self.published_pk).confidence_scale_version, "v1")
        self.assertEqual(
            ExperimentVersion.objects.get(pk=self.version_pk).configuration["confidence_scale_version"],
            "v1",
        )
        self.assertEqual(Experiment.objects.create(name="迁移后新实验").confidence_scale_version, "v2")
