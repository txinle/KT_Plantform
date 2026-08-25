from __future__ import annotations

import hashlib
import json

from .models import ItemInstance


def canonical_presentation_hash(
    *,
    presentation_variant,
    option_order,
    parameter_values,
    random_seed,
):
    canonical = json.dumps(
        {
            "option_order": option_order,
            "parameter_values": parameter_values,
            "presentation_variant": presentation_variant,
            "random_seed": random_seed,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def get_or_create_default_item_instance(question_snapshot):
    option_order = [option["key"] for option in question_snapshot.options]
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
