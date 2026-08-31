import django.db.models.deletion
import apps.question_bank.models
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("question_bank", "0006_concept_parent"),
    ]

    operations = [
        migrations.CreateModel(
            name="ImportPreviewBatch",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("kind", models.CharField(choices=[("course_kc", "课程 KC"), ("question_kc", "题目 KC")], max_length=24)),
                ("session_binding", models.CharField(max_length=64)),
                ("file_sha256", models.CharField(max_length=64)),
                ("payload", models.JSONField(default=dict)),
                ("summary", models.JSONField(default=dict)),
                ("status", models.CharField(choices=[("pending", "待确认"), ("consumed", "已消费")], default="pending", max_length=16)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(default=apps.question_bank.models.import_preview_expiry)),
                ("consumed_at", models.DateTimeField(blank=True, null=True)),
                ("bank", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name="import_previews", to="question_bank.questionbank")),
                ("course", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="question_bank_import_previews", to="accounts.course")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="question_bank_import_previews", to=settings.AUTH_USER_MODEL)),
            ],
            options={"ordering": ["-created_at"]},
        ),
        migrations.AddIndex(
            model_name="importpreviewbatch",
            index=models.Index(fields=["status", "expires_at"], name="qb_preview_status_exp"),
        ),
        migrations.AddConstraint(
            model_name="concept",
            constraint=models.CheckConstraint(
                condition=models.Q(("course__isnull", True), ("code__regex", "^KC[1-9][0-9]*$"), _connector="OR"),
                name="course_kc_code_canonical",
            ),
        ),
    ]
