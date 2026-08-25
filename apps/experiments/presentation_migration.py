import hashlib
import json


def _presentation_hash(presentation):
    canonical = json.dumps(
        presentation,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def backfill_default_item_instances(apps, schema_editor):
    QuestionSnapshot = apps.get_model("experiments", "QuestionSnapshot")
    ItemInstance = apps.get_model("experiments", "ItemInstance")
    StudentQuestionAssignment = apps.get_model(
        "assessment",
        "StudentQuestionAssignment",
    )

    for question_snapshot in QuestionSnapshot.objects.order_by("pk").iterator():
        presentation = {
            "presentation_variant": "default",
            "option_order": [option["key"] for option in question_snapshot.options],
            "parameter_values": {},
            "random_seed": "",
        }
        item_instance, _created = ItemInstance.objects.get_or_create(
            question_snapshot_id=question_snapshot.pk,
            presentation_hash=_presentation_hash(presentation),
            defaults=presentation,
        )
        StudentQuestionAssignment.objects.filter(
            snapshot_id=question_snapshot.pk,
            item_instance_id__isnull=True,
        ).update(item_instance_id=item_instance.pk)


def noop_reverse(apps, schema_editor):
    pass
