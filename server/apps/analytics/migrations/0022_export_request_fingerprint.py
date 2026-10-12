from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0021_student_comparison_members"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportjob",
            name="request_fingerprint",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
    ]
