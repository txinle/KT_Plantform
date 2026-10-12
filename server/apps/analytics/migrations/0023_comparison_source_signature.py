from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('analytics', '0022_export_request_fingerprint')]

    operations = [
        migrations.AddField(
            model_name='studentclasstaskcomparison',
            name='source_signature',
            field=models.CharField(max_length=64, blank=True, default='', db_default=''),
        ),
    ]
