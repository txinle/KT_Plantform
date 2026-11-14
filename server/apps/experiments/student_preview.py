"""Read-only teacher trial paper; never creates student or publication records."""
import hashlib
from decimal import Decimal

from apps.question_bank.services.question_groups import material_segments
from apps.question_bank.services.legacy_fill import legacy_blank_count
from apps.question_bank.services.question_images import with_question_images
from .models import ReleaseSnapshotQuestionMapping
from .presentation import options_for_snapshot
from .publication_services import _preview_candidates, _select_questions


def student_preview_projection(experiment):
    candidates = _preview_candidates(experiment)
    def seed(purpose):
        return hashlib.sha256(f"teacher-preview-v1:{experiment.pk}:{purpose}".encode()).hexdigest()
    selected = _select_questions(experiment, candidates, seed("sample"), seed("order"))
    units, groups = [], {}
    for row in selected:
        candidate = row["candidate"]
        frozen = isinstance(candidate, ReleaseSnapshotQuestionMapping)
        question = candidate.question_snapshot if frozen else candidate
        group = question.group_snapshot if frozen else question.group
        if question.question_type == "true_false":
            options = [{"key": "true", "text": "正确"}, {"key": "false", "text": "错误"}]
        elif frozen:
            options = [{"key": option.key, "text": option.text} for option in options_for_snapshot(question)]
        else:
            options = list(question.options.order_by("key", "pk").values("key", "text"))
        content = with_question_images({
            "item_key": str(question.pk), "question_type": question.question_type,
            "answer_blank_count": legacy_blank_count(question.question_type, question.text, grouped=bool(group)),
            "educational_type": question.educational_type, "title": question.title,
            "instructions": question.instructions, "text": question.text, "options": options,
            "max_score": format(question.max_score, ".2f"),
            "group_order": question.group_order, "blank_id": question.blank_id,
        })
        if group:
            if group.pk not in groups:
                projection = with_question_images({
                    "group_id": str(group.pk), "kind": group.kind, "title": group.title,
                    "instructions": group.instructions, "material": group.material,
                    "content_language": group.content_language, "word_bank": list(group.word_bank),
                    "members": [],
                })
                unit = {"key": f"group-{group.pk}", "kind": group.kind, "question": None, "group": projection}
                groups[group.pk] = (unit, group)
                units.append(unit)
            groups[group.pk][0]["group"]["members"].append(content)
        else:
            units.append({"key": f"item-{question.pk}", "kind": question.educational_type if question.educational_type in ("translation", "essay") else question.question_type,
                          "question": content, "group": None, "maximum_score": content["max_score"]})
    for unit, group in groups.values():
        members = unit["group"]["members"]
        unit["group"]["material_segments"] = material_segments(group, members)
        unit["maximum_score"] = format(sum((Decimal(member["max_score"]) for member in members), Decimal("0")), ".2f")
    return {
        "experiment_id": experiment.pk, "experiment_name": experiment.name,
        "source": "frozen" if experiment.frozen_release_snapshot_id else "draft",
        "units": units,
    }
