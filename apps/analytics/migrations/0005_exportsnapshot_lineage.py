from django.db import migrations, models
import django.utils.timezone


class Migration(migrations.Migration):
    dependencies = [("analytics", "0004_alter_exportjob_kind")]

    operations = [
        migrations.AddField(model_name="exportsnapshot", name="source_cutoff_at", field=models.DateTimeField(default=django.utils.timezone.now)),
        migrations.AddField(model_name="exportsnapshot", name="event_high_watermark", field=models.PositiveBigIntegerField(default=0)),
        migrations.AddField(model_name="exportsnapshot", name="schema_manifest_hash", field=models.CharField(blank=True, default="", max_length=64)),
        migrations.AddField(model_name="exportsnapshot", name="invalidated_at", field=models.DateTimeField(blank=True, null=True)),
        migrations.AddField(model_name="exportsnapshot", name="invalidated_reason", field=models.CharField(blank=True, default="", max_length=255)),
    ]
