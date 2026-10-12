from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0020_experiment_sampling_configuration"),
    ]

    operations = [
        migrations.AddField(
            model_name="questionsnapshot",
            name="primary_kc_id_snapshot",
            field=models.PositiveBigIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="primary_kc_path",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="primary_kc_levels",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="all_kc_paths",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="all_kc_snapshots",
            field=models.JSONField(default=list),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="root_kc_code",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="kc_mapping_snapshot",
            field=models.JSONField(default=dict),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="kc_mapping_schema_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="questionsnapshot",
            name="kc_mapping_hash",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
