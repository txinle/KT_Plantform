import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0007_recording_coverage_v1"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportsnapshot",
            name="coverage_run",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="export_snapshots",
                to="analytics.recordingcoveragerun",
            ),
        ),
    ]
