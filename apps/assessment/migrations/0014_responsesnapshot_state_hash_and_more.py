"""Backfill logical response state without inventing any missing events."""

import hashlib
import json

import django.core.validators
from django.db import migrations, models


def backfill_logical_state(apps, schema_editor):
    ResponseSnapshot = apps.get_model("assessment", "ResponseSnapshot")
    for snapshot in ResponseSnapshot.objects.filter(state_hash__isnull=True).iterator():
        question_type = "short_answer"
        try:
            question_type = snapshot.attempt.assignment.snapshot.question_type
        except Exception:
            pass
        state = {"question_type": question_type, "value": snapshot.content}
        canonical = json.dumps(state, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
        ResponseSnapshot.objects.filter(pk=snapshot.pk).update(
            state_json=state,
            state_hash=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        )


class Migration(migrations.Migration):

    dependencies = [
        ('assessment', '0013_submission_grade_contract'),
    ]

    operations = [
        migrations.AddField(
            model_name='responsesnapshot',
            name='state_hash',
            field=models.CharField(blank=True, max_length=64, null=True, validators=[django.core.validators.RegexValidator(message='内容哈希必须是 64 位小写十六进制 SHA-256。', regex='^[0-9a-f]{64}$')]),
        ),
        migrations.AddField(
            model_name='responsesnapshot',
            name='state_json',
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_logical_state, migrations.RunPython.noop),
    ]
