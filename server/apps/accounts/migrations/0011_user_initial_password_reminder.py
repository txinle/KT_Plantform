from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("accounts", "0010_user_must_change_password")]

    operations = [
        migrations.AddField(
            model_name="user",
            name="uses_initial_password",
            field=models.BooleanField(default=False, verbose_name="正在使用初始密码"),
        ),
        migrations.AddField(
            model_name="user",
            name="initial_password_reminder_shown_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="初始密码提醒展示时间"
            ),
        ),
        migrations.AddField(
            model_name="user",
            name="initial_password_state_recorded_at",
            field=models.DateTimeField(
                blank=True, null=True, verbose_name="初始密码状态确认时间"
            ),
        ),
    ]
