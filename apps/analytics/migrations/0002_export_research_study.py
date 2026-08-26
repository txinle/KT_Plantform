from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [("analytics", "0001_initial"), ("accounts", "0007_datapurgerequest")]
    operations = [
        migrations.AddField(
            model_name="exportsnapshot",
            name="research_study",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="export_snapshots", to="accounts.researchstudy"),
        ),
        migrations.AddField(
            model_name="exportjob",
            name="research_study",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name="export_jobs", to="accounts.researchstudy"),
        ),
    ]
