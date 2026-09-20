from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("assessment", "0024_experimentsession_next_ingest_sequence_no"),
    ]

    operations = [
        migrations.AddField(
            model_name="experimentsession",
            name="research_source_revision",
            field=models.PositiveBigIntegerField(db_index=True, default=0),
        ),
    ]
