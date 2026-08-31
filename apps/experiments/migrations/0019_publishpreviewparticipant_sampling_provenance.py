from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0018_remove_publishplanclass_schedule_configured"),
    ]

    operations = [
        migrations.AddField(
            model_name="publishpreviewparticipant",
            name="sampling_seed",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="publishpreviewparticipant",
            name="ordering_seed",
            field=models.CharField(default="", max_length=64),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="publishpreviewparticipant",
            name="sampling_algorithm_version",
            field=models.CharField(default="task_root_hmac_v2", max_length=64),
        ),
        migrations.AddField(
            model_name="publishpreviewitem",
            name="sampling_source",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
