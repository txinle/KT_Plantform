from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0008_exportsnapshot_coverage_run"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportsnapshot",
            name="source_bundle",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="exportsnapshot",
            name="source_bundle_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
