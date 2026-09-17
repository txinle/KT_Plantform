from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0014_courseenrollmentaccessperiod"),
    ]

    operations = [
        migrations.AddField(
            model_name="studentimportjob",
            name="transferred",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="studentimportjob",
            name="skipped",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
