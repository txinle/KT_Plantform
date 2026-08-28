from collections import defaultdict

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

    snapshots_by_pair = defaultdict(list)
    snapshots = (
        ExperimentClassSnapshot.objects.exclude(source_class_group_id__isnull=True)
        .select_related("version__experiment", "source_class_group")
        .order_by(
            "version__experiment_id",
            "source_class_group_id",
            "version_id",
            "class_snapshot_id",
        )
    )
    for snapshot in snapshots.iterator():
        experiment_id = snapshot.version.experiment_id
        snapshots_by_pair[(snapshot.source_class_group_id, experiment_id)].append(
            snapshot
        )

    existing_task_pairs = set(
        ClassExperimentTask.objects.order_by("class_group_id", "experiment_id").values_list(
            "class_group_id",
            "experiment_id",
        )
    )

    for (class_group_id, experiment_id), pair_snapshots in sorted(
        snapshots_by_pair.items()
    ):
        if (class_group_id, experiment_id) in existing_task_pairs:
            continue

        version_ids = {snapshot.version_id for snapshot in pair_snapshots}
        if len(version_ids) != 1:
            continue
        version_id = next(iter(version_ids))
        version = pair_snapshots[0].version
        class_group = pair_snapshots[0].source_class_group

        if class_group.course_id != version.experiment.course_id:
            continue

        matching_snapshots = list(
            ExperimentClassSnapshot.objects.filter(
                version_id=version_id,
                source_class_group_id=class_group_id,
            )
            .order_by("class_snapshot_id")
            .iterator()
        )
        if len(matching_snapshots) != 1:
            continue
        class_snapshot = matching_snapshots[0]

        ClassExperimentTask.objects.create(
            class_group_id=class_group_id,
            experiment_id=experiment_id,
            release_snapshot_id=version_id,
            class_snapshot_id=class_snapshot.pk,
            published_at=version.published_at,
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
