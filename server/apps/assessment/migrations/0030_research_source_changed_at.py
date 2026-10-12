from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("assessment", "0029_student_comparison")]

    operations = [
        migrations.AddField(
            model_name="experimentsession",
            name="research_source_changed_at",
            field=models.DateTimeField(null=True, blank=True),
        ),
    ]
