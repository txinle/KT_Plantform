from importlib import import_module
from types import SimpleNamespace
from unittest.mock import patch

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.test import TransactionTestCase


class ClassExperimentTaskBackfillMigrationTests(TransactionTestCase):
    """The historical migration must create facts only where identity is certain."""

    reset_sequences = True
    migrate_from = ("experiments", "0013_alter_experiment_options")
    migrate_to = ("experiments", "0014_backfill_class_experiment_tasks")

    @staticmethod
    def _targets_with_experiments_at(executor, experiments_target):
        targets = []
        for target in executor.loader.graph.leaf_nodes():
            if target[0] == "experiments":
                targets.append(experiments_target)
            elif target[0] == "assessment":
                # New assessment deadline fields depend on A+ experiment
                # migrations.  This historical 0014 migration test must keep
                # assessment at its last compatible leaf while it deliberately
                # rewinds experiments to 0013/0014.
                targets.append(("assessment", "0021_session_terminal_constraint"))
            else:
                targets.append(target)
        return targets

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.executor = MigrationExecutor(connection)
        cls.current_targets = cls.executor.loader.graph.leaf_nodes()
        cls.old_targets = cls._targets_with_experiments_at(
            cls.executor,
            cls.migrate_from,
        )
        cls.executor.migrate(cls.old_targets)
        old_apps = cls.executor.loader.project_state(cls.old_targets).apps

        User = old_apps.get_model("accounts", "User")
        Course = old_apps.get_model("accounts", "Course")
        ClassGroup = old_apps.get_model("accounts", "ClassGroup")
        QuestionBank = old_apps.get_model("question_bank", "QuestionBank")
        Experiment = old_apps.get_model("experiments", "Experiment")
        ExperimentVersion = old_apps.get_model("experiments", "ExperimentVersion")
        ExperimentClassSnapshot = old_apps.get_model(
            "experiments",
            "ExperimentClassSnapshot",
        )
        ClassExperimentTask = old_apps.get_model(
            "experiments",
            "ClassExperimentTask",
        )

        teacher = User.objects.create(
            username="class-task-backfill-teacher",
            role="teacher",
        )
        course = Course.objects.create(
            owner_id=teacher.pk,
            name="历史回填课程",
            code="BACKFILL101",
            term="2026 秋",
        )
        other_course = Course.objects.create(
            owner_id=teacher.pk,
            name="历史回填跨课程",
            code="BACKFILL102",
            term="2026 秋",
        )
        class_group = ClassGroup.objects.create(
            course_id=course.pk,
            name="可回填班",
            academic_year="2023",
        )
        other_class_group = ClassGroup.objects.create(
            course_id=other_course.pk,
            name="跨课程班",
            academic_year="2023",
        )

        def experiment(name):
            bank = QuestionBank.objects.create(
                owner_id=teacher.pk,
                created_by_id=teacher.pk,
                course_id=course.pk,
                name=f"{name}题库",
            )
            return Experiment.objects.create(
                owner_id=teacher.pk,
                created_by_id=teacher.pk,
                course_id=course.pk,
                question_bank_id=bank.pk,
                name=name,
                status="published",
            )

        def version_and_snapshot(experiment_row, source_class_group):
            version = ExperimentVersion.objects.create(
                experiment_id=experiment_row.pk,
                version_number=ExperimentVersion.objects.filter(
                    experiment_id=experiment_row.pk
                ).count()
                + 1,
                published_by_id=teacher.pk,
            )
            snapshot = ExperimentClassSnapshot.objects.create(
                version_id=version.pk,
                source_class_group_id=source_class_group.pk
                if source_class_group is not None
                else None,
                name_snapshot=(source_class_group.name if source_class_group else "旧标签"),
                academic_year_snapshot=(
                    source_class_group.academic_year if source_class_group else ""
                ),
                capture_mode=(
                    "native" if source_class_group is not None else "legacy_label_projection"
                ),
            )
            return version, snapshot

        eligible_experiment = experiment("唯一合法历史发布")
        eligible_version, eligible_snapshot = version_and_snapshot(
            eligible_experiment,
            class_group,
        )

        multiple_experiment = experiment("多版本历史发布")
        multiple_first_version, _multiple_first_snapshot = version_and_snapshot(
            multiple_experiment,
            class_group,
        )
        multiple_second_version, _multiple_second_snapshot = version_and_snapshot(
            multiple_experiment,
            class_group,
        )

        source_null_experiment = experiment("无来源班级历史发布")
        source_null_version, source_null_snapshot = version_and_snapshot(
            source_null_experiment,
            None,
        )

        cross_course_experiment = experiment("跨课程历史发布")
        cross_course_version, cross_course_snapshot = version_and_snapshot(
            cross_course_experiment,
            other_class_group,
        )

        existing_experiment = experiment("已有正式任务历史发布")
        existing_version, existing_snapshot = version_and_snapshot(
            existing_experiment,
            class_group,
        )
        existing_task = ClassExperimentTask.objects.create(
            class_group_id=class_group.pk,
            experiment_id=existing_experiment.pk,
            release_snapshot_id=existing_version.pk,
            class_snapshot_id=existing_snapshot.pk,
            published_at=existing_version.published_at,
        )

        inconsistent_experiment = experiment("已有不一致任务历史发布")
        inconsistent_version, inconsistent_snapshot = version_and_snapshot(
            inconsistent_experiment,
            class_group,
        )
        unrelated_experiment = experiment("错误任务发布快照")
        unrelated_version, _unrelated_snapshot = version_and_snapshot(
            unrelated_experiment,
            class_group,
        )
        inconsistent_task = ClassExperimentTask.objects.create(
            class_group_id=class_group.pk,
            experiment_id=inconsistent_experiment.pk,
            release_snapshot_id=unrelated_version.pk,
            class_snapshot_id=inconsistent_snapshot.pk,
            published_at=inconsistent_version.published_at,
        )

        cls.eligible = {
            "class_group_id": class_group.pk,
            "experiment_id": eligible_experiment.pk,
            "version_id": eligible_version.pk,
            "snapshot_id": eligible_snapshot.pk,
            "published_at": eligible_version.published_at,
        }
        cls.multiple = {
            "class_group_id": class_group.pk,
            "experiment_id": multiple_experiment.pk,
            "version_ids": {multiple_first_version.pk, multiple_second_version.pk},
        }
        cls.source_null = {
            "experiment_id": source_null_experiment.pk,
            "version_id": source_null_version.pk,
            "snapshot_id": source_null_snapshot.pk,
        }
        cls.cross_course = {
            "class_group_id": other_class_group.pk,
            "experiment_id": cross_course_experiment.pk,
            "version_id": cross_course_version.pk,
            "snapshot_id": cross_course_snapshot.pk,
        }
        cls.existing = {
            "task_id": existing_task.pk,
            "class_group_id": class_group.pk,
            "experiment_id": existing_experiment.pk,
            "version_id": existing_version.pk,
            "snapshot_id": existing_snapshot.pk,
        }
        cls.inconsistent = {
            "task_id": inconsistent_task.pk,
            "class_group_id": class_group.pk,
            "experiment_id": inconsistent_experiment.pk,
            "release_snapshot_id": unrelated_version.pk,
            "class_snapshot_id": inconsistent_snapshot.pk,
            "published_at": inconsistent_version.published_at,
        }

        cls.executor = MigrationExecutor(connection)
        cls.new_targets = cls._targets_with_experiments_at(
            cls.executor,
            cls.migrate_to,
        )
        cls.executor.migrate(cls.new_targets)
        cls.apps = cls.executor.loader.project_state(cls.new_targets).apps

    @classmethod
    def tearDownClass(cls):
        try:
            MigrationExecutor(connection).migrate(cls.current_targets)
        finally:
            super().tearDownClass()

    def test_backfill_creates_only_the_unique_eligible_task_and_reverse_keeps_it(self):
        ClassExperimentTask = self.apps.get_model(
            "experiments",
            "ClassExperimentTask",
        )

        eligible_task = ClassExperimentTask.objects.get(
            class_group_id=self.eligible["class_group_id"],
            experiment_id=self.eligible["experiment_id"],
        )
        self.assertEqual(eligible_task.release_snapshot_id, self.eligible["version_id"])
        self.assertEqual(eligible_task.class_snapshot_id, self.eligible["snapshot_id"])
        self.assertEqual(eligible_task.published_at, self.eligible["published_at"])

        self.assertFalse(
            ClassExperimentTask.objects.filter(
                class_group_id=self.multiple["class_group_id"],
                experiment_id=self.multiple["experiment_id"],
            ).exists()
        )
        self.assertFalse(
            ClassExperimentTask.objects.filter(
                experiment_id=self.source_null["experiment_id"],
            ).exists()
        )
        self.assertFalse(
            ClassExperimentTask.objects.filter(
                class_group_id=self.cross_course["class_group_id"],
                experiment_id=self.cross_course["experiment_id"],
            ).exists()
        )
        self.assertEqual(
            ClassExperimentTask.objects.filter(
                class_group_id=self.existing["class_group_id"],
                experiment_id=self.existing["experiment_id"],
            ).count(),
            1,
        )
        existing_task = ClassExperimentTask.objects.get(pk=self.existing["task_id"])
        self.assertEqual(existing_task.release_snapshot_id, self.existing["version_id"])
        self.assertEqual(existing_task.class_snapshot_id, self.existing["snapshot_id"])

        inconsistent_task = ClassExperimentTask.objects.get(
            pk=self.inconsistent["task_id"]
        )
        self.assertEqual(
            inconsistent_task.release_snapshot_id,
            self.inconsistent["release_snapshot_id"],
        )
        self.assertEqual(
            inconsistent_task.class_snapshot_id,
            self.inconsistent["class_snapshot_id"],
        )
        self.assertEqual(
            inconsistent_task.published_at,
            self.inconsistent["published_at"],
        )

        migration_module = import_module(
            "apps.experiments.migrations.0014_backfill_class_experiment_tasks"
        )
        with (
            patch.object(
                ClassExperimentTask.objects,
                "using",
                wraps=ClassExperimentTask.objects.using,
            ) as task_using,
            patch.object(
                self.apps.get_model("experiments", "ExperimentClassSnapshot").objects,
                "using",
                wraps=self.apps.get_model(
                    "experiments", "ExperimentClassSnapshot"
                ).objects.using,
            ) as snapshot_using,
        ):
            migration_module.backfill_class_experiment_tasks(
                self.apps,
                SimpleNamespace(connection=SimpleNamespace(alias="default")),
            )
        task_using.assert_any_call("default")
        snapshot_using.assert_any_call("default")

        executor = MigrationExecutor(connection)
        executor.migrate(self.old_targets)
        old_apps = executor.loader.project_state(self.old_targets).apps
        HistoricalTask = old_apps.get_model("experiments", "ClassExperimentTask")
        self.assertTrue(HistoricalTask.objects.filter(pk=eligible_task.pk).exists())
        self.assertTrue(
            HistoricalTask.objects.filter(pk=self.existing["task_id"]).exists()
        )
        self.assertTrue(
            HistoricalTask.objects.filter(pk=self.inconsistent["task_id"]).exists()
        )

    def test_backfill_uses_schema_alias_for_snapshot_reads_and_task_writes(self):
        aliases = []
        created_tasks = []

        class TaskQuerySet:
            def order_by(self, *fields):
                return self

            def values_list(self, *fields):
                return ()

            def bulk_create(self, objects, batch_size):
                created_tasks.extend(objects)

        task_query_set = TaskQuerySet()

        class TaskManager:
            def using(self, alias):
                aliases.append(("task", alias))
                return task_query_set

        class HistoricalTask:
            objects = TaskManager()

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        class SnapshotQuerySet:
            def exclude(self, **kwargs):
                return self

            def order_by(self, *fields):
                return self

            def values(self, *fields):
                return self

            def iterator(self):
                return iter(
                    [
                        {
                            "version_id": 17,
                            "class_snapshot_id": "snapshot-17",
                            "source_class_group_id": 11,
                            "source_class_group__course_id": 3,
                            "version__experiment_id": 5,
                            "version__experiment__course_id": 3,
                            "version__published_at": "2026-08-28T00:00:00Z",
                        }
                    ]
                )

        snapshot_query_set = SnapshotQuerySet()

        class SnapshotManager:
            def using(self, alias):
                aliases.append(("snapshot", alias))
                return snapshot_query_set

        class HistoricalSnapshot:
            objects = SnapshotManager()

        class HistoricalApps:
            @staticmethod
            def get_model(app_label, model_name):
                return {
                    "ClassExperimentTask": HistoricalTask,
                    "ExperimentClassSnapshot": HistoricalSnapshot,
                }[model_name]

        migration_module = import_module(
            "apps.experiments.migrations.0014_backfill_class_experiment_tasks"
        )
        migration_module.backfill_class_experiment_tasks(
            HistoricalApps(),
            SimpleNamespace(connection=SimpleNamespace(alias="historical")),
        )

        self.assertEqual(
            aliases,
            [("task", "historical"), ("snapshot", "historical"), ("task", "historical")],
        )
        self.assertEqual(len(created_tasks), 1)
        self.assertEqual(
            created_tasks[0].kwargs,
            {
                "class_group_id": 11,
                "experiment_id": 5,
                "release_snapshot_id": 17,
                "class_snapshot_id": "snapshot-17",
                "published_at": "2026-08-28T00:00:00Z",
            },
        )
