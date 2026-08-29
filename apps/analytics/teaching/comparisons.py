from __future__ import annotations


def competition_ranks(values):
    """Rank comparable, unrounded values as 1, 2, 2, 4."""
    ranked = sorted(
        (item for item in values if item[1] is not None),
        key=lambda item: (-item[1], item[2], item[0]),
    )
    ranks = {}
    previous_value = None
    previous_rank = None
    for position, (identifier, value, _secondary) in enumerate(ranked, start=1):
        if value != previous_value:
            previous_rank = position
            previous_value = value
        ranks[identifier] = previous_rank
    return ranks
