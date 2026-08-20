from django.db import migrations


def redact_question_bank_visibility_audits(apps, schema_editor):
    AuditLog = apps.get_model("audit", "AuditLog")
    QuestionBank = apps.get_model("question_bank", "QuestionBank")
    bank_course_ids = {
        str(bank_id): course_id
        for bank_id, course_id in QuestionBank.objects.values_list("pk", "course_id")
    }

    for audit in AuditLog.objects.filter(action="question_bank.visibility").iterator():
        details = audit.details if isinstance(audit.details, dict) else {}
        raw_bank_id = details.get("bank_id", audit.object_id)
        try:
            bank_id = int(raw_bank_id)
        except (TypeError, ValueError):
            bank_id = raw_bank_id
        audit.details = {
            "bank_id": bank_id,
            "course_id": bank_course_ids.get(str(bank_id)),
            "changed_fields": ["visibility"],
        }
        audit.save(update_fields=["details"])


class Migration(migrations.Migration):
    dependencies = [
        ("audit", "0001_initial"),
        ("question_bank", "0002_add_course_scope"),
    ]

    operations = [
        migrations.RunPython(
            redact_question_bank_visibility_audits,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
