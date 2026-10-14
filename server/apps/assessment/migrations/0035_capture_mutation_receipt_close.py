from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('assessment', '0034_capture_mutation_receipt')]

    operations = [
        migrations.AlterField(
            model_name='capturemutationreceipt', name='kind',
            field=models.CharField(choices=[('draft', '草稿'), ('tool', '工具'), ('close', '关闭')],
                editable=False, max_length=8),
        ),
    ]
