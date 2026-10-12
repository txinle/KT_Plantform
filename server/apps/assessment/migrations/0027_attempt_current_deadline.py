from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0026_session_recovery_round"),
    ]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="current_effective_deadline_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
