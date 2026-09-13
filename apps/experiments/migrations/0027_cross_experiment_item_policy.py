from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("experiments", "0026_confidence_scale_v2")]

    operations = [
        migrations.AddField(
            model_name="experiment",
            name="cross_experiment_item_policy",
            field=models.CharField(
                choices=[
                    ("exclude_previous", "排除已正式作答题目"),
                    ("mixed_review", "混合复测"),
                    ("unrestricted", "不限制历史题目"),
                ],
                default="unrestricted",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="experiment",
            name="review_count",
            field=models.PositiveIntegerField(default=0),
        ),
    ]
