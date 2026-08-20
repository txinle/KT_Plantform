def bank_metadata_audit_details(bank, changed_fields):
    return {
        "bank_id": bank.pk,
        "course_id": bank.course_id,
        "changed_fields": list(changed_fields),
    }
