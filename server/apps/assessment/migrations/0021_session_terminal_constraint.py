from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0020_recording_capture_contract_v1"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="experimentsession",
            constraint=models.CheckConstraint(
                condition=(
                    Q(status="in_progress", terminal_at__isnull=True)
                    | Q(
                        status__in=("completed", "expired", "invalidated"),
                        terminal_at__isnull=False,
                    )
                ),
                name="session_terminal_at_matches_status",
            ),
        ),
    ]
