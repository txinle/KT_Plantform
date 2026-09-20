import django.db.models.deletion
import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0011_researchsessionmaterialization_quality_rows"),
    ]

    operations = [
        migrations.CreateModel(
            name="ResearchExportAsset",
            fields=[
                (
                    "asset_id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("course", "课程研究资产"),
                            ("experiment_sessions", "实验 Session 资产"),
                        ],
                        max_length=32,
                    ),
                ),
                (
                    "contract_version",
                    models.CharField(default="research-export-v2", max_length=32),
                ),
                ("content_hash", models.CharField(max_length=64)),
                ("artifact_path", models.CharField(max_length=512)),
                ("artifact_sha256", models.CharField(max_length=64)),
                ("row_counts", models.JSONField(default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "course",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="research_export_assets",
                        to="accounts.course",
                    ),
                ),
                (
                    "experiment",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="research_export_assets",
                        to="experiments.experiment",
                    ),
                ),
            ],
            options={"ordering": ["created_at", "asset_id"]},
        ),
        migrations.AddConstraint(
            model_name="researchexportasset",
            constraint=models.UniqueConstraint(
                fields=(
                    "kind",
                    "course",
                    "experiment",
                    "contract_version",
                    "content_hash",
                ),
                name="analytics_unique_research_export_asset",
                nulls_distinct=False,
            ),
        ),
    ]
