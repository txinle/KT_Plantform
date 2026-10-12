from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0010_research_session_materialization"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchsessionmaterialization",
            name="quality_rows",
            field=models.JSONField(default=list),
        ),
    ]
