from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0027_attempt_current_deadline"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="active_seconds_at_recovery",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
