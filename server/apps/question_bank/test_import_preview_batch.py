from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from apps.accounts.models import Course, User

from .models import QuestionBank


class ImportPreviewBatchContractTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="preview-owner",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="导入课程",
            code="IMPORT-PREVIEW",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="导入题库",
        )

    def test_preview_persists_payload_and_is_bound_to_actor_session_and_scope(self):
        from .services.preview_batch import ImportPreviewBatchService

        batch = ImportPreviewBatchService.create(
            kind="question_kc",
            course=self.course,
            bank=self.bank,
            actor=self.teacher,
            session_key="session-a",
            file_bytes=b"four-column-csv",
            payload={"rows": [{"sequence": 1}]},
            summary={"questions": 1},
        )

        self.assertEqual(batch.payload["rows"][0]["sequence"], 1)
        self.assertNotEqual(batch.session_binding, "session-a")
        self.assertEqual(len(batch.file_sha256), 64)
        with self.assertRaisesMessage(Exception, "预览批次与当前会话不匹配") as raised:
            ImportPreviewBatchService.consume(
                batch_id=batch.pk,
                kind="question_kc",
                course=self.course,
                bank=self.bank,
                actor=self.teacher,
                session_key="session-b",
            )
        self.assertEqual(raised.exception.code, "preview_binding_mismatch")

    def test_preview_is_consumed_once_and_expired_batches_return_conflict(self):
        from .services.preview_batch import ImportPreviewBatchConflict, ImportPreviewBatchService

        batch = ImportPreviewBatchService.create(
            kind="course_kc",
            course=self.course,
            bank=None,
            actor=self.teacher,
            session_key="session-a",
            file_bytes=b"course-kc-csv",
            payload={"rows": []},
            summary={},
        )

        consumed = ImportPreviewBatchService.consume(
            batch_id=batch.pk,
            kind="course_kc",
            course=self.course,
            bank=None,
            actor=self.teacher,
            session_key="session-a",
        )
        self.assertIsNotNone(consumed.consumed_at)
        with self.assertRaises(ImportPreviewBatchConflict) as raised:
            ImportPreviewBatchService.consume(
                batch_id=batch.pk,
                kind="course_kc",
                course=self.course,
                bank=None,
                actor=self.teacher,
                session_key="session-a",
            )
        self.assertEqual(raised.exception.code, "preview_consumed")

        expired = ImportPreviewBatchService.create(
            kind="course_kc",
            course=self.course,
            bank=None,
            actor=self.teacher,
            session_key="session-a",
            file_bytes=b"expired",
            payload={"rows": []},
            summary={},
        )
        type(expired).objects.filter(pk=expired.pk).update(
            expires_at=timezone.now() - timedelta(seconds=1)
        )
        with self.assertRaises(ImportPreviewBatchConflict) as raised:
            ImportPreviewBatchService.consume(
                batch_id=expired.pk,
                kind="course_kc",
                course=self.course,
                bank=None,
                actor=self.teacher,
                session_key="session-a",
            )
        self.assertEqual(raised.exception.code, "preview_expired")
