from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("experiments", "0036_deletion_receipt_retains_deleted_course")]
    operations = [migrations.AddField(model_name="experiment", name="sampling_type_ranges",
                                     field=models.JSONField(default=list, blank=True))]
