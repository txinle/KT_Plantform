from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("experiments", "0028_publishoperationjob"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="schedulerevision",
            name="total_time_limit_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schedulerevision",
            name="per_question_time_limit_seconds",
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="schedulerevision",
            name="reopens_manual_close",
            field=models.BooleanField(default=False),
        ),
        migrations.CreateModel(
            name="TaskManualClosure",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("closed_at", models.DateTimeField()),
                ("schedule_revision_no", models.PositiveIntegerField()),
                ("reason", models.CharField(max_length=500)),
                ("class_task", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="manual_closures", to="experiments.classexperimenttask")),
                ("created_by", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="task_manual_closures", to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
