from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('assessment', '0035_capture_mutation_receipt_close')]
    operations = [migrations.AddField(
        model_name='experimentsession', name='transition_protocol_version',
        field=models.PositiveSmallIntegerField(default=0, db_default=0, editable=False),
    )]
