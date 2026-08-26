from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0002_export_research_study"),
    ]

    operations = [
        migrations.AddField(
            model_name="exportsnapshot",
            name="filter_definition",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
