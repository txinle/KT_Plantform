from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0025_experimentsession_research_source_revision"),
        ("experiments", "0029_task_time_revisions"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentsession",
            name="recovery_round",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="experimentsession",
            name="personal_deadline_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assessmentevent",
            name="recovery_round",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.RemoveConstraint(model_name="assessmentevent", name="unique_session_terminal_event"),
        migrations.RemoveConstraint(model_name="assessmentevent", name="unique_attempt_closed_event"),
        migrations.AddConstraint(
            model_name="assessmentevent",
            constraint=models.UniqueConstraint(
                fields=("session", "recovery_round"),
                condition=Q(event_type__in=("session_completed", "session_expired", "session_invalidated")),
                name="unique_session_terminal_event",
            ),
        ),
        migrations.AddConstraint(
            model_name="assessmentevent",
            constraint=models.UniqueConstraint(
                fields=("attempt", "recovery_round"),
                condition=Q(attempt__isnull=False, event_type="attempt_closed"),
                name="unique_attempt_closed_event",
            ),
        ),
    ]
