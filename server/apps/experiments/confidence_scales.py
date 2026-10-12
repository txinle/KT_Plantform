CONFIDENCE_SCALE_REGISTRY = {
    "v1": {"minimum": 0, "maximum": 100, "step": 10},
    "v2": {"minimum": 0, "maximum": 100, "step": 1},
}

CONFIDENCE_SCALE_CHOICES = (
    ("v1", "v1（10% 步进）"),
    ("v2", "v2（1% 步进）"),
)


def confidence_scale(version):
    try:
        return CONFIDENCE_SCALE_REGISTRY[version]
    except (KeyError, TypeError) as error:
        raise ValueError("Unknown confidence scale version.") from error
