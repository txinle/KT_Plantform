import hashlib
import json
from dataclasses import dataclass
from typing import Any

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


def _canonical_json(value: Any) -> str:
    """Stable JSON representation used for logical response-state hashes."""
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True)
class ResponseState:
    """Logical response state, deliberately independent of snapshot identity."""

    question_type: str
    value: Any

    @property
    def state_json(self) -> dict:
        return {"question_type": self.question_type, "value": self.value}

    @property
    def canonical_json(self) -> str:
        return _canonical_json(self.state_json)

    @property
    def state_hash(self) -> str:
        return hashlib.sha256(self.canonical_json.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ResponseStateRef:
    """Reference to persisted text state; hash remains a logical-state hash."""

    snapshot_id: Any
    state_hash: str
    content_length: int
    schema_version: int = 1
    content_hash: str | None = None

    def to_payload(self) -> dict:
        payload = {
            "snapshot_id": str(self.snapshot_id),
            "state_hash": self.state_hash,
            "content_length": self.content_length,
            "schema_version": self.schema_version,
        }
        if self.content_hash is not None:
            payload["content_hash"] = self.content_hash
        return payload


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


def canonical_response_state(question_type, answer):
    """Return the canonical *raw* state for every supported question type.

    Numeric values intentionally retain their submitted lexical form; scoring
    normalization must not alter the process-state observation.
    """
    if question_type in CHOICE_QUESTION_TYPES:
        return canonical_choice_response_state(question_type, answer)
    if question_type in TEXT_LIKE_QUESTION_TYPES:
        if answer is None:
            return ""
        return answer if isinstance(answer, (str, int, float, bool)) else str(answer)
    raise ValueError(f"Unsupported response question type: {question_type}")


def create_response_snapshot(*, attempt, content, schema_version=1, response_state=None, batch_context=None):
    with transaction.atomic(savepoint=batch_context is None):
        return _create_response_snapshot(attempt=attempt, content=content,
            schema_version=schema_version, response_state=response_state, batch_context=batch_context)


def _create_response_snapshot(*, attempt, content, schema_version, response_state, batch_context):
    if not isinstance(content, str):
        raise TypeError("Response snapshot content must be text.")
    question_type = getattr(getattr(attempt, "assignment", None), "snapshot", None)
    question_type = getattr(question_type, "question_type", "short_answer")
    logical_state = response_state or ResponseState(
        question_type,
        canonical_response_state(question_type, content),
    )
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    key = (attempt.pk, logical_state.state_hash)
    existing = (batch_context['snapshots'].get(key) if batch_context is not None else
                ResponseSnapshot.objects.filter(attempt=attempt, state_hash=logical_state.state_hash).first())
    if existing is not None:
        if (
            existing.content != content
            or existing.content_length != len(content)
            or existing.schema_version != schema_version
            or existing.state_hash != logical_state.state_hash
        ):
            raise ValueError(
                "Existing response content hash has conflicting snapshot metadata."
            )
        if batch_context is not None:
            batch_context['snapshots'][key] = existing
        return existing

    snapshot = ResponseSnapshot(
        attempt=attempt,
        content=content,
        content_hash=content_hash,
        content_length=len(content),
        state_json=logical_state.state_json,
        state_hash=logical_state.state_hash,
        schema_version=schema_version,
    )
    if batch_context is not None and 'new_snapshots' in batch_context:
        snapshot.full_clean(exclude=('attempt',), validate_unique=False, validate_constraints=False)
        batch_context['new_snapshots'].append(snapshot)
        batch_context['snapshots'][key] = snapshot
        return snapshot
    try:
        with transaction.atomic():
            snapshot.save(force_insert=True)
    except IntegrityError:
        existing = ResponseSnapshot.objects.filter(
            attempt=attempt,
            state_hash=logical_state.state_hash,
        ).first()
        if existing is None:
            raise
        if existing.state_hash != logical_state.state_hash:
            raise ValueError("Existing response state hash has conflicting snapshot metadata.")
        return existing
    if batch_context is not None:
        batch_context['snapshots'][key] = snapshot
    return snapshot
