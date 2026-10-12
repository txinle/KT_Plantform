from __future__ import annotations

from .models import (
    ItemInstance,
    canonical_presentation_hash,
    presentation_option_keys,
)


def get_or_create_default_item_instance(question_snapshot):
    option_order = presentation_option_keys(question_snapshot)
    presentation = {
        "presentation_variant": "default",
        "option_order": option_order,
        "parameter_values": {},
        "random_seed": "",
    }
    presentation_hash = canonical_presentation_hash(**presentation)
    return ItemInstance.objects.get_or_create(
        question_snapshot=question_snapshot,
        presentation_hash=presentation_hash,
        defaults=presentation,
    )[0]
