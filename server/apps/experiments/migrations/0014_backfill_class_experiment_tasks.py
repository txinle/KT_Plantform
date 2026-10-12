from itertools import groupby

from django.db import migrations


def backfill_class_experiment_tasks(apps, schema_editor):
    """Create only task rows whose historical class identity is unambiguous.

    This deliberately uses the migration registry rather than current runtime
    models: formal tasks are normally write-protected, while a data migration
    must be able to record the historical facts it has proved.  Any ambiguity
    is retained for the read-only audit command rather than guessed here.
    """

    ClassExperimentTask = apps.get_model("experiments", "ClassExperimentTask")
    ExperimentClassSnapshot = apps.get_model(
        "experiments",
        "ExperimentClassSnapshot",
    )

    database_alias = schema_editor.connection.alias
    existing_task_pairs = set(
        ClassExperimentTask.objects.using(database_alias)
        .order_by("class_group_id", "experiment_id")
        .values_list("class_group_id", "experiment_id")
    )
    snapshots = (
        ExperimentClassSnapshot.objects.using(database_alias)
        .exclude(source_class_group_id__isnull=True)
        .order_by(
            "source_class_group_id",
            "version__experiment_id",
            "version_id",
            "class_snapshot_id",
        )
        .values(
            "version_id",
            "class_snapshot_id",
            "source_class_group_id",
            "source_class_group__course_id",
            "version__experiment_id",
            "version__experiment__course_id",
            "version__published_at",
        )
    )
    pending_tasks = []
    for (class_group_id, experiment_id), pair_snapshots_iterator in groupby(
        snapshots.iterator(),
        key=lambda snapshot: (
            snapshot["source_class_group_id"],
            snapshot["version__experiment_id"],
        ),
    ):
        snapshot = None
        snapshot_count = 0
        version_ids = set()
        for pair_snapshot in pair_snapshots_iterator:
            snapshot_count += 1
            version_ids.add(pair_snapshot["version_id"])
            if snapshot is None:
                snapshot = pair_snapshot
        if (class_group_id, experiment_id) in existing_task_pairs:
            continue

        if len(version_ids) != 1:
            continue
        if snapshot_count != 1:
            continue
        if (
            snapshot["source_class_group__course_id"]
            != snapshot["version__experiment__course_id"]
        ):
            continue
        pending_tasks.append(
            ClassExperimentTask(
                class_group_id=class_group_id,
                experiment_id=experiment_id,
                release_snapshot_id=snapshot["version_id"],
                class_snapshot_id=snapshot["class_snapshot_id"],
                published_at=snapshot["version__published_at"],
            )
        )
        if len(pending_tasks) == 500:
            ClassExperimentTask.objects.using(database_alias).bulk_create(
                pending_tasks,
                batch_size=500,
            )
            pending_tasks.clear()
    if pending_tasks:
        ClassExperimentTask.objects.using(database_alias).bulk_create(
            pending_tasks,
            batch_size=500,
        )


class Migration(migrations.Migration):

    dependencies = [
        ("experiments", "0013_alter_experiment_options"),
    ]

    operations = [
        migrations.RunPython(
            backfill_class_experiment_tasks,
            migrations.RunPython.noop,
        ),
    ]
