from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0021_questionsnapshot_kc_provenance"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentversionparticipantsnapshot",
            name="assignment_snapshot",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="experimentversionparticipantsnapshot",
            name="ordering_seed",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="experimentversionparticipantsnapshot",
            name="sampling_algorithm_version",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
        migrations.AddField(
            model_name="experimentversionparticipantsnapshot",
            name="sampling_seed",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
    ]
