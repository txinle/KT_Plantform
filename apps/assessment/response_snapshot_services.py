import hashlib
import json

from django.db import IntegrityError, transaction

from apps.question_bank.models import Question
from apps.question_bank.services.scoring import normalize_true_false_answer

from .models import ResponseSnapshot


TEXT_LIKE_QUESTION_TYPES = frozenset(
    {
        Question.Type.NUMERIC,
        Question.Type.SHORT,
        Question.Type.FILL_BLANK,
    }
)
CHOICE_QUESTION_TYPES = frozenset(
    {
        Question.Type.SINGLE,
        Question.Type.MULTIPLE,
        Question.Type.TRUE_FALSE,
    }
)


def response_snapshot_content(answer):
    if answer is None:
        return ""
    if isinstance(answer, str):
        return answer
    return json.dumps(
        answer,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_choice_response_state(question_type, answer):
    if question_type == Question.Type.MULTIPLE:
        if not isinstance(answer, (list, tuple, set)):
            return [] if answer is None else [str(answer)]
        return sorted({str(value) for value in answer})
    if question_type == Question.Type.TRUE_FALSE:
        normalized = normalize_true_false_answer(answer)
        if normalized is None:
            return None
        return "true" if normalized else "false"
    if question_type == Question.Type.SINGLE:
        return None if answer is None else str(answer)
    raise ValueError("Question type does not use a direct choice response state.")


@transaction.atomic
def create_response_snapshot(*, attempt, content, schema_version=1):
    if not isinstance(content, str):
        raise TypeError("Response snapshot content must be text.")
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    existing = ResponseSnapshot.objects.filter(
        attempt=attempt,
        content_hash=content_hash,
    ).first()
    if existing is not None:
        if (
            existing.content != content
            or existing.content_length != len(content)
            or existing.schema_version != schema_version
        ):
            raise ValueError(
                "Existing response content hash has conflicting snapshot metadata."
            )
        return existing

    snapshot = ResponseSnapshot(
        attempt=attempt,
        content=content,
        content_hash=content_hash,
        content_length=len(content),
        schema_version=schema_version,
    )
    try:
        with transaction.atomic():
            snapshot.save(force_insert=True)
    except IntegrityError:
        existing = ResponseSnapshot.objects.filter(
            attempt=attempt,
            content_hash=content_hash,
        ).first()
        if existing is None:
            raise
        return existing
    return snapshot
