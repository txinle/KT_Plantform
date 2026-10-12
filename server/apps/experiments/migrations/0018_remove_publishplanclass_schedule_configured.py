from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0017_participant_task_uniqueness"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="publishplanclass",
            name="schedule_configured",
        ),
    ]
