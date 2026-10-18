import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


def enqueue_legacy_incomplete_cleanups(apps, schema_editor):
    purges = apps.get_model("accounts", "DataPurgeRequest")
    jobs = apps.get_model("analytics", "ResearchArtifactCleanupJob")
    alias = schema_editor.connection.alias
    for cleanup in purges.objects.using(alias).filter(scope_type="research_artifacts").exclude(status="executed").iterator():
        jobs.objects.using(alias).get_or_create(cleanup_id=cleanup.pk, defaults={
            # Older code overwrote this list. Only its actual surviving scope is known.
            "remaining_files": cleanup.scope_definition.get("files", []),
            "last_error": str(cleanup.result_counts.get("error", "")),
        })


def require_drained_queue_before_reverse(apps, schema_editor):
    jobs = apps.get_model("analytics", "ResearchArtifactCleanupJob")
    if jobs.objects.using(schema_editor.connection.alias).exclude(state="completed").exists():
        raise RuntimeError("文件清理队列尚未排空；停止消费者并完成任务交接后才能回退此迁移。")


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0024_research_publication_sequence"),
        ("accounts", "0007_datapurgerequest"),
    ]
    operations = [
        migrations.CreateModel(
            name="ResearchArtifactCleanupJob",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("state", models.CharField(choices=[("pending", "等待清理"), ("running", "清理中"), ("completed", "已完成"), ("failed", "清理失败")], default="pending", max_length=12)),
                ("remaining_files", models.JSONField(default=list)),
                ("retained_files", models.JSONField(default=list)),
                ("attempts", models.PositiveSmallIntegerField(default=0)),
                ("next_attempt_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("lease_token", models.UUIDField(blank=True, null=True)),
                ("lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("last_error", models.TextField(blank=True, default="")),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("cleanup", models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name="artifact_cleanup_job", to="accounts.datapurgerequest")),
            ],
            options={"indexes": [
                models.Index(fields=["state", "next_attempt_at"], name="artifact_cleanup_due_idx"),
                models.Index(fields=["state", "lease_expires_at"], name="artifact_cleanup_lease_idx"),
            ]},
        ),
        migrations.RunPython(enqueue_legacy_incomplete_cleanups, require_drained_queue_before_reverse),
    ]
