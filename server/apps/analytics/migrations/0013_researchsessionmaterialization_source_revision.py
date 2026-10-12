from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("analytics", "0012_research_export_asset"),
    ]

    operations = [
        migrations.AddField(
            model_name="researchsessionmaterialization",
            name="source_revision",
            field=models.PositiveBigIntegerField(db_index=True, default=0),
        ),
    ]
