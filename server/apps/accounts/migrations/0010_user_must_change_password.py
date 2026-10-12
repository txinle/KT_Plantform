from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0009_class_student_analytics_policy")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="must_change_password",
            field=models.BooleanField(default=False, verbose_name="必须修改密码"),
        ),
    ]
