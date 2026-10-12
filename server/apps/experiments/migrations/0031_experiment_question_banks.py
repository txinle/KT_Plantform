from django.db import migrations, models
import django.db.models.deletion


def backfill_question_banks(apps, schema_editor):
    Experiment = apps.get_model("experiments", "Experiment")
    ExperimentQuestionBank = apps.get_model("experiments", "ExperimentQuestionBank")
    rows = [
        ExperimentQuestionBank(
            experiment_id=experiment.pk,
            question_bank_id=experiment.question_bank_id,
            selection_order=0,
        )
        for experiment in Experiment.objects.using(schema_editor.connection.alias).filter(course_id__isnull=False, question_bank_id__isnull=False).only(
            "pk", "question_bank_id"
        )
    ]
    ExperimentQuestionBank.objects.using(schema_editor.connection.alias).bulk_create(rows, ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [("experiments", "0030_clarify_personal_feedback_release")]

    operations = [
        migrations.CreateModel(
            name="ExperimentQuestionBank",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("selection_order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("experiment", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="question_bank_links", to="experiments.experiment")),
                ("question_bank", models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name="experiment_links", to="question_bank.questionbank")),
            ],
            options={"ordering": ["selection_order", "question_bank_id"]},
        ),
        migrations.AddConstraint(
            model_name="experimentquestionbank",
            constraint=models.UniqueConstraint(fields=("experiment", "question_bank"), name="unique_experiment_question_bank"),
        ),
        migrations.AddConstraint(
            model_name="experimentquestionbank",
            constraint=models.UniqueConstraint(fields=("experiment", "selection_order"), name="unique_experiment_question_bank_order"),
        ),
        migrations.RunPython(backfill_question_banks, migrations.RunPython.noop),
    ]
