import uuid

import django.db.models.deletion
from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ("analytics", "0006_export_tombstones_and_scope"),
        ("assessment", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="RecordingCoverageRun",
            fields=[
                ("run_id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("scope_type", models.CharField(default="session", max_length=32)),
                ("scope_definition", models.JSONField(default=dict)),
                ("scope_hash", models.CharField(max_length=64)),
                ("coverage_version", models.CharField(default="coverage-v1", max_length=32)),
                ("algorithm_version", models.CharField(default="recording-coverage-v1", max_length=64)),
                ("code_version", models.CharField(blank=True, max_length=64)),
                ("parameter_document", models.JSONField(default=dict)),
                ("parameter_hash", models.CharField(max_length=64)),
                ("source_cutoff_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("source_high_water_marks", models.JSONField(default=dict)),
                ("capture_contract_hashes", models.JSONField(default=list)),
                ("source_hash", models.CharField(max_length=64)),
                (
                    "run_status",
                    models.CharField(
                        choices=[("building", "生成中"), ("completed", "已完成"), ("failed", "失败")],
                        default="completed",
                        max_length=16,
                    ),
                ),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-generated_at"]},
        ),
        migrations.CreateModel(
            name="RecordingCoverageSession",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "overall_status",
                    models.CharField(
                        choices=[
                            ("complete", "完整"),
                            ("partial", "部分"),
                            ("insufficient", "证据不足"),
                            ("invalid", "无效"),
                            ("not_applicable", "不适用"),
                            ("legacy_unknown", "历史未知"),
                        ],
                        max_length=24,
                    ),
                ),
                ("lifecycle_status", models.CharField(default="not_applicable", max_length=24)),
                ("navigation_status", models.CharField(default="not_applicable", max_length=24)),
                ("client_sequence_status", models.CharField(default="not_applicable", max_length=24)),
                ("time_sync_status", models.CharField(default="not_applicable", max_length=24)),
                ("telemetry_status", models.CharField(default="not_applicable", max_length=24)),
                ("client_instance_count", models.PositiveIntegerField(default=0)),
                ("sequence_gap_count", models.PositiveIntegerField(default=0)),
                ("missing_sequence_ranges", models.JSONField(default=list)),
                ("tail_gap_detected", models.BooleanField(default=False)),
                ("missing_reasons", models.JSONField(default=list)),
                ("analysis_eligibility", models.JSONField(default=dict)),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="session_results", to="analytics.recordingcoveragerun")),
                ("session", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recording_coverage_results", to="assessment.experimentsession")),
            ],
        ),
        migrations.CreateModel(
            name="RecordingCoverageAttempt",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("overall_status", models.CharField(max_length=24)),
                ("enter_status", models.CharField(default="not_applicable", max_length=24)),
                ("render_status", models.CharField(default="not_applicable", max_length=24)),
                ("leave_status", models.CharField(default="not_applicable", max_length=24)),
                ("response_chain_status", models.CharField(default="not_applicable", max_length=24)),
                ("final_state_status", models.CharField(default="not_applicable", max_length=24)),
                ("time_quality", models.CharField(default="unknown", max_length=32)),
                ("response_state_count", models.PositiveIntegerField(default=0)),
                ("sequence_gap_count", models.PositiveIntegerField(default=0)),
                ("missing_reasons", models.JSONField(default=list)),
                ("analysis_eligibility", models.JSONField(default=dict)),
                ("derived_features", models.JSONField(default=dict)),
                ("generated_at", models.DateTimeField(auto_now_add=True)),
                ("attempt", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="recording_coverage_results", to="assessment.attempt")),
                ("run", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="attempt_results", to="analytics.recordingcoveragerun")),
            ],
        ),
        migrations.AddConstraint(
            model_name="recordingcoveragesession",
            constraint=models.UniqueConstraint(fields=("run", "session"), name="unique_coverage_run_session"),
        ),
        migrations.AddConstraint(
            model_name="recordingcoverageattempt",
            constraint=models.UniqueConstraint(fields=("run", "attempt"), name="unique_coverage_run_attempt"),
        ),
    ]
