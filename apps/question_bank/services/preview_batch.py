from __future__ import annotations

import hashlib

from django.core.signing import salted_hmac
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from ..models import ImportPreviewBatch


class ImportPreviewBatchConflict(Exception):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _session_binding(session_key: str) -> str:
    return salted_hmac(
        "question-bank-import-preview",
        session_key or "",
        algorithm="sha256",
    ).hexdigest()


class ImportPreviewBatchService:
    @staticmethod
    def create(
        *,
        kind,
        course,
        bank,
        actor,
        session_key,
        file_bytes,
        payload,
        summary,
    ):
        if kind not in ImportPreviewBatch.Kind.values:
            raise ValueError("未知的预览批次类型")
        if (kind in (ImportPreviewBatch.Kind.QUESTION_KC, ImportPreviewBatch.Kind.QUESTION_CONTENT)) != (bank is not None):
            raise ValueError("题目 KC 预览必须绑定题库，课程 KC 预览不得绑定题库")
        if bank is not None and bank.course_id != course.pk:
            raise ValueError("预览题库不属于当前课程")
        return ImportPreviewBatch.objects.create(
            kind=kind,
            course=course,
            bank=bank,
            created_by=actor,
            session_binding=_session_binding(session_key),
            file_sha256=hashlib.sha256(file_bytes).hexdigest(),
            payload=payload,
            summary=summary,
        )

    @staticmethod
    def _locked_valid_batch(*, batch_id, kind, course, bank, actor, session_key, allow_consumed=False):
        try:
            batch = ImportPreviewBatch.objects.select_for_update().get(pk=batch_id)
        except (ImportPreviewBatch.DoesNotExist, ValueError, TypeError, ValidationError):
            raise ImportPreviewBatchConflict(
                "preview_not_found",
                "预览批次不存在或已经失效",
            ) from None
        expected_bank_id = bank.pk if bank is not None else None
        if (
            batch.kind != kind
            or batch.course_id != course.pk
            or batch.bank_id != expected_bank_id
            or batch.created_by_id != actor.pk
            or batch.session_binding != _session_binding(session_key)
        ):
            raise ImportPreviewBatchConflict(
                "preview_binding_mismatch",
                "预览批次与当前会话不匹配",
            )
        if batch.status == ImportPreviewBatch.Status.CONSUMED:
            if allow_consumed:
                return batch
            raise ImportPreviewBatchConflict("preview_consumed", "预览批次已经确认，不能重复消费")
        if batch.expires_at <= timezone.now():
            raise ImportPreviewBatchConflict("preview_expired", "预览批次已过期，请重新上传")
        return batch

    @classmethod
    @transaction.atomic
    def consume(cls, **binding):
        batch = cls._locked_valid_batch(**binding)
        batch.status = ImportPreviewBatch.Status.CONSUMED
        batch.consumed_at = timezone.now()
        batch.save(update_fields=["status", "consumed_at"])
        return batch

    @classmethod
    @transaction.atomic
    def confirm(cls, *, apply, **binding):
        batch = cls._locked_valid_batch(**binding)
        result = apply(batch.payload)
        batch.status = ImportPreviewBatch.Status.CONSUMED
        batch.consumed_at = timezone.now()
        batch.save(update_fields=["status", "consumed_at"])
        return batch, result
