from importlib import import_module

from django.db import migrations
from django.db.migrations.loader import MigrationLoader
from django.db.models.deletion import SET_NULL
from django.test import SimpleTestCase


class APlusMigrationContractTests(SimpleTestCase):
    @staticmethod
    def _initial_schema_apps():
        loader = MigrationLoader(None)
        return loader.project_state(
            [("experiments", "0015_a_plus_publish_plan")]
        ).apps

    def test_schema_chain_retains_a_plus_and_allows_unassigned_draft_bank(self):
        loader = MigrationLoader(None)

        self.assertEqual(
            loader.graph.leaf_nodes("experiments"),
            [("experiments", "0026_confidence_scale_v2")],
        )
        self.assertIn(
            ("experiments", "0022_participant_snapshot_assignment_provenance"),
            loader.graph.forwards_plan(("experiments", "0026_confidence_scale_v2")),
        )
        experiment = loader.project_state().apps.get_model("experiments", "Experiment")
        self.assertTrue(experiment._meta.get_field("question_bank").null)

    def test_a_plus_initial_schema_migration_has_no_data_backfill(self):
        migration_module = import_module(
            "apps.experiments.migrations.0015_a_plus_publish_plan"
        )

        self.assertFalse(
            any(
                isinstance(operation, migrations.RunPython)
                for operation in migration_module.Migration.operations
            )
        )

    def test_initial_schema_state_has_publish_plan_default_schedule_fields(self):
        PublishPlan = self._initial_schema_apps().get_model(
            "experiments", "PublishPlan"
        )

        for field_name in ("default_opens_at", "default_closes_at"):
            with self.subTest(field_name=field_name):
                field = PublishPlan._meta.get_field(field_name)
                self.assertTrue(field.null)
                self.assertTrue(field.blank)

        self.assertIn(
            "publish_plan_default_schedule_order",
            {constraint.name for constraint in PublishPlan._meta.constraints},
        )

        step_constraint = next(
            constraint
            for constraint in PublishPlan._meta.constraints
            if constraint.name == "publish_plan_step_range"
        )
        self.assertIn("lte", str(step_constraint.condition))
        self.assertIn("2", str(step_constraint.condition))

    def test_initial_schema_tracks_explicit_schedule_configuration(self):
        PublishPlanClass = self._initial_schema_apps().get_model(
            "experiments", "PublishPlanClass"
        )

        field = PublishPlanClass._meta.get_field("schedule_configured")

        self.assertFalse(field.default)

    def test_initial_schema_state_makes_preview_plan_class_nullable_set_null(self):
        PublishPreviewClassResult = self._initial_schema_apps().get_model(
            "experiments", "PublishPreviewClassResult"
        )
        field = PublishPreviewClassResult._meta.get_field("publish_plan_class")

        self.assertTrue(field.null)
        self.assertIs(field.remote_field.on_delete, SET_NULL)

    def test_initial_schema_state_keeps_task_lifecycle_fields_non_null(self):
        ClassExperimentTask = self._initial_schema_apps().get_model(
            "experiments", "ClassExperimentTask"
        )
        terminal_reason = ClassExperimentTask._meta.get_field("terminal_reason")
        provenance = ClassExperimentTask._meta.get_field("provenance")

        self.assertFalse(terminal_reason.null)
        self.assertEqual(terminal_reason.default, "")
        self.assertFalse(provenance.null)
        self.assertEqual(provenance.default, "native")

    def test_assessment_deadline_contract_depends_on_a_plus_leaf(self):
        migration_module = import_module(
            "apps.assessment.migrations.0022_task_deadline_contract"
        )

        self.assertIn(
            ("experiments", "0017_participant_task_uniqueness"),
            migration_module.Migration.dependencies,
        )
