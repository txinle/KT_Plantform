from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0019_publishpreviewparticipant_sampling_provenance"),
        ("question_bank", "0006_concept_parent"),
    ]

    operations = [
        migrations.AddField(
            model_name="experiment",
            name="sampling_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("total_count", "按总题数抽题"),
                    ("kc_quota", "按知识点配额抽题"),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="experiment",
            name="sampling_total_count",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="experiment",
            name="sampling_match_scope",
            field=models.CharField(
                choices=[("primary", "主 KC"), ("any_level", "任意层级")],
                default="any_level",
                max_length=16,
            ),
        ),
        migrations.CreateModel(
            name="ExperimentSamplingKC",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("quota", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "match_scope",
                    models.CharField(
                        choices=[("primary", "主 KC"), ("any_level", "任意层级")],
                        default="primary",
                        max_length=16,
                    ),
                ),
                (
                    "concept",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="experiment_sampling_rules",
                        to="question_bank.concept",
                    ),
                ),
                (
                    "experiment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="sampling_kcs",
                        to="experiments.experiment",
                    ),
                ),
            ],
            options={"ordering": ["concept_id"]},
        ),
        migrations.AddConstraint(
            model_name="experimentsamplingkc",
            constraint=models.UniqueConstraint(
                fields=("experiment", "concept"),
                name="unique_experiment_sampling_kc",
            ),
        ),
        migrations.AddConstraint(
            model_name="experimentsamplingkc",
            constraint=models.CheckConstraint(
                condition=models.Q(("quota__isnull", True), ("quota__gt", 0), _connector="OR"),
                name="experiment_sampling_kc_positive_quota",
            ),
        ),
    ]
