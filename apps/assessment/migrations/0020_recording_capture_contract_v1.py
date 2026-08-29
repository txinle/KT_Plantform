import uuid

import apps.assessment.models
import django.core.validators
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings
from django.db import migrations, models
from django.db.models import F, Q


def backfill_recording_anchors(apps, schema_editor):
    Session = apps.get_model("assessment", "ExperimentSession")
    AssessmentEvent = apps.get_model("assessment", "AssessmentEvent")

    for session in Session.objects.exclude(status="in_progress").iterator():
        terminal_at = session.completed_at or session.last_activity_at or session.started_at
        Session.objects.filter(pk=session.pk).update(terminal_at=terminal_at)

    for event in AssessmentEvent.objects.filter(student__isnull=True).iterator():
        AssessmentEvent.objects.filter(pk=event.pk).update(
            student_id=event.session.student_id
        )


class Migration(migrations.Migration):

    dependencies = [
        ("assessment", "0019_response_snapshot_state_hash_uniqueness"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="CaptureContractArtifact",
            fields=[
                (
                    "capture_contract_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("contract_name", models.CharField(max_length=64, unique=True)),
                ("contract_version", models.PositiveSmallIntegerField()),
                ("schema_document", models.JSONField()),
                (
                    "schema_sha256",
                    models.CharField(
                        max_length=64,
                        validators=[
                            django.core.validators.RegexValidator(
                                message="内容哈希必须是 64 位小写十六进制 SHA-256。",
                                regex="^[0-9a-f]{64}$",
                            )
                        ],
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["contract_name"]},
        ),
        migrations.AddConstraint(
            model_name="capturecontractartifact",
            constraint=models.CheckConstraint(
                condition=Q(contract_version__gte=1),
                name="capture_contract_version_positive",
            ),
        ),
        migrations.AddField(
            model_name="experimentsession",
            name="capture_contract",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sessions",
                to="assessment.capturecontractartifact",
            ),
        ),
        migrations.AddField(
            model_name="experimentsession",
            name="terminal_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="assessmentevent",
            name="student",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assessment_events",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="responseinputobservation",
            name="source_event",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="response_input_observation",
                to="assessment.assessmentevent",
            ),
        ),
        migrations.AddField(
            model_name="responseinputobservation",
            name="batch_start_client_monotonic_ms",
            field=models.DecimalField(
                blank=True, decimal_places=3, max_digits=20, null=True
            ),
        ),
        migrations.AddField(
            model_name="responseinputobservation",
            name="batch_end_client_monotonic_ms",
            field=models.DecimalField(
                blank=True, decimal_places=3, max_digits=20, null=True
            ),
        ),
        migrations.AddField(
            model_name="responseinputobservation",
            name="is_ime",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="ClientCaptureCheckpoint",
            fields=[
                (
                    "checkpoint_id",
                    models.UUIDField(
                        default=uuid.uuid4, editable=False, primary_key=True, serialize=False
                    ),
                ),
                ("client_instance_id", models.UUIDField()),
                ("highest_emitted_sequence_no", models.PositiveBigIntegerField(default=0)),
                ("highest_acked_sequence_no", models.PositiveBigIntegerField(default=0)),
                (
                    "highest_contiguous_acked_sequence_no",
                    models.PositiveBigIntegerField(default=0),
                ),
                ("pending_event_count", models.PositiveIntegerField(default=0)),
                ("failed_event_count", models.PositiveIntegerField(default=0)),
                ("missing_local_sequence_ranges", models.JSONField(blank=True, default=list)),
                ("client_monotonic_ms", models.DecimalField(decimal_places=3, max_digits=20)),
                ("client_wall_time_utc", models.DateTimeField()),
                ("client_time_origin", models.DateTimeField()),
                (
                    "trigger",
                    models.CharField(
                        choices=[
                            ("heartbeat", "心跳"),
                            ("navigation", "导航"),
                            ("commit", "提交"),
                            ("pagehide", "页面隐藏"),
                            ("resume", "恢复"),
                            ("session_end", "会话结束"),
                            ("ack_progress", "确认进度"),
                        ],
                        max_length=20,
                    ),
                ),
                (
                    "quality_flags",
                    models.JSONField(
                        blank=True,
                        default=list,
                        validators=[apps.assessment.models.validate_quality_flags],
                    ),
                ),
                ("app_version", models.CharField(blank=True, max_length=64)),
                (
                    "server_received_at",
                    models.DateTimeField(default=django.utils.timezone.now),
                ),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="client_capture_checkpoints",
                        to="assessment.experimentsession",
                    ),
                ),
                (
                    "student",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="client_capture_checkpoints",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "time_sync",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="capture_checkpoints",
                        to="assessment.clienttimesync",
                    ),
                ),
            ],
            options={
                "ordering": ["session_id", "client_instance_id", "server_received_at"],
            },
        ),
        migrations.RunPython(backfill_recording_anchors, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="assessmentevent",
            name="student",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="assessment_events",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddIndex(
            model_name="clientcapturecheckpoint",
            index=models.Index(
                fields=["session", "client_instance_id", "server_received_at"],
                name="capture_ckpt_session_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="clientcapturecheckpoint",
            constraint=models.CheckConstraint(
                condition=Q(highest_contiguous_acked_sequence_no__lte=F("highest_acked_sequence_no")),
                name="checkpoint_contiguous_lte_acked",
            ),
        ),
        migrations.AddConstraint(
            model_name="clientcapturecheckpoint",
            constraint=models.CheckConstraint(
                condition=Q(highest_acked_sequence_no__lte=F("highest_emitted_sequence_no")),
                name="checkpoint_acked_lte_emitted",
            ),
        ),
        migrations.AddConstraint(
            model_name="responseinputobservation",
            constraint=models.CheckConstraint(
                condition=(
                    Q(
                        batch_start_client_monotonic_ms__isnull=True,
                        batch_end_client_monotonic_ms__isnull=True,
                    )
                    | Q(
                        batch_start_client_monotonic_ms__isnull=False,
                        batch_end_client_monotonic_ms__isnull=False,
                        batch_end_client_monotonic_ms__gte=F(
                            "batch_start_client_monotonic_ms"
                        ),
                    )
                ),
                name="input_observation_batch_interval_valid",
            ),
        ),
    ]
