from django.db import migrations

from apps.accounts.course_migration import backfill_legacy_courses, noop_reverse


class Migration(migrations.Migration):
    dependencies = [
        ("accounts", "0002_add_courses"),
        ("question_bank", "0002_add_course_scope"),
        ("experiments", "0002_add_course_scope"),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_courses, noop_reverse),
    ]
