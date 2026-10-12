import django.db.models.deletion
from django.db import migrations, models

from apps.experiments.presentation_migration import (
    backfill_default_item_instances,
    noop_reverse,
)
from apps.accounts.research_subjects import backfill_course_subjects


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0006_research_subject_ids"),
        ("assessment", "0002_experimentsession_sampling_seeds"),
        ("experiments", "0006_item_instance_confidence"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentquestionassignment",
            name="item_instance",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assignments",
                to="experiments.iteminstance",
            ),
        ),
        migrations.RunPython(backfill_default_item_instances, noop_reverse),
        migrations.RunPython(backfill_course_subjects, noop_reverse),
        migrations.AlterField(
            model_name="studentquestionassignment",
            name="item_instance",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assignments",
                to="experiments.iteminstance",
            ),
        ),
    ]
