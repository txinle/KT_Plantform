from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("assessment", "0012_lifecycle_event_cardinality")]

    operations = [
        migrations.AddField(
            model_name="attempt",
            name="current_response_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="current_for_attempts",
                to="assessment.responsesnapshot",
            ),
        ),
        migrations.AddField(
            model_name="attempt",
            name="final_response_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="final_for_attempts",
                to="assessment.responsesnapshot",
            ),
        ),
        migrations.AddField(
            model_name="submissionevent",
            name="final_response_snapshot",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="submissions",
                to="assessment.responsesnapshot",
            ),
        ),
        migrations.AddField(
            model_name="submissionevent",
            name="grading_status",
            field=models.CharField(
                choices=[
                    ("pending", "等待评分"),
                    ("completed", "评分完成"),
                    ("regrade", "待重新评分"),
                ],
                default="completed",
                max_length=16,
            ),
        ),
        migrations.AlterField(
            model_name="submissionevent",
            name="is_correct",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="submissionevent",
            name="score",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True),
        ),
        migrations.CreateModel(
            name="GradeRecord",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("version", models.PositiveIntegerField(default=1)),
                ("status", models.CharField(choices=[("pending", "等待评分"), ("completed", "评分完成"), ("regrade", "待重新评分")], default="pending", max_length=16)),
                ("is_correct", models.BooleanField(blank=True, null=True)),
                ("score", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("max_score", models.DecimalField(blank=True, decimal_places=2, max_digits=5, null=True)),
                ("normalized_response", models.JSONField(blank=True, null=True)),
                ("grading_method", models.CharField(default="automatic", max_length=32)),
                ("grading_version", models.CharField(blank=True, default="", max_length=64)),
                ("grader_version", models.CharField(blank=True, default="", max_length=64)),
                ("graded_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("submission", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="grade_records", to="assessment.submissionevent")),
                ("supersedes_grade", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="superseding_grades", to="assessment.graderecord")),
            ],
            options={"ordering": ["submission_id", "-version"]},
        ),
        migrations.AddConstraint(
            model_name="graderecord",
            constraint=models.UniqueConstraint(fields=("submission", "version"), name="unique_submission_grade_version"),
        ),
        migrations.AddConstraint(
            model_name="graderecord",
            constraint=models.CheckConstraint(condition=models.Q(("version__gte", 1)), name="grade_record_version_positive"),
        ),
    ]
