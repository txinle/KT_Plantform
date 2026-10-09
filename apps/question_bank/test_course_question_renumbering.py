from io import StringIO
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from threading import Event
from unittest.mock import patch

from django.core.management import call_command
from django.db import close_old_connections, connections, transaction
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.test.utils import CaptureQueriesContext

from apps.accounts.models import Course, User
from apps.experiments.models import (
    Experiment,
    ExperimentQuestion,
    ExperimentVersion,
    QuestionSnapshot,
)

from .models import CourseQuestionIdentity, Question, QuestionBank
from .services.course_question_identity import (
    CourseQuestionRenumberingBlocked,
    renumber_course_question_identities,
)
from .services.lifecycle import ReferencedQuestionError, delete_question, delete_question_bank


class CourseQuestionRenumberingTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="numbering-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="编号测试课程",
            code="NUMBERING",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="编号测试题库",
        )

    def question(self, sequence, *, bank=None):
        return Question.objects.create(
            bank=bank or self.bank,
            sequence=sequence,
            question_type=Question.Type.SHORT,
            text=f"题目 {sequence}",
            correct_answer=f"答案 {sequence}",
        )

    @patch("apps.question_bank.services.course_question_identity._retire_exports")
    @patch("apps.question_bank.services.course_question_identity._invalidate_draft_previews")
    def test_partial_delete_compacts_course_numbers_without_changing_question_pks(
        self,
        invalidate_previews,
        retire_exports,
    ):
        questions = [self.question(index) for index in range(1, 5)]
        original_pks = [question.pk for question in questions]
        retire_exports.return_value = {
            "export_count": 2,
            "cleanup_pending": False,
            "cleanup_id": None,
        }

        result = delete_question(questions[1])

        remaining = list(
            CourseQuestionIdentity.objects.filter(course=self.course)
            .order_by("course_sequence")
            .values_list("question_id", "course_sequence", "stable_code")
        )
        self.assertEqual(
            remaining,
            [
                (original_pks[0], 1, f"C{self.course.pk:06d}-Q000001"),
                (original_pks[2], 2, f"C{self.course.pk:06d}-Q000002"),
                (original_pks[3], 3, f"C{self.course.pk:06d}-Q000003"),
            ],
        )
        self.assertFalse(CourseQuestionIdentity.objects.filter(question__isnull=True).exists())
        self.assertEqual(result["renumbered_question_count"], 2)
        self.assertEqual(result["retired_export_count"], 2)
        retire_exports.assert_called_once_with(
            course_id=self.course.pk,
            question_ids=[original_pks[1], original_pks[2], original_pks[3]],
            reason="course_question_deleted",
        )
        invalidate_previews.assert_called_once()

    @patch("apps.question_bank.services.course_question_identity._retire_exports")
    @patch("apps.question_bank.services.course_question_identity._invalidate_draft_previews")
    def test_full_190_question_clear_removes_tombstones_and_restarts_at_q001(
        self,
        invalidate_previews,
        retire_exports,
    ):
        retire_exports.return_value = {
            "export_count": 0,
            "cleanup_pending": False,
            "cleanup_id": None,
        }
        for index in range(1, 191):
            self.question(index)

        delete_question_bank(self.bank)
        replacement_bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="重新上传题库",
        )
        replacement = self.question(1, bank=replacement_bank)

        self.assertEqual(replacement.course_identity.course_sequence, 1)
        self.assertEqual(
            replacement.course_identity.stable_code,
            f"C{self.course.pk:06d}-Q000001",
        )
        self.assertEqual(CourseQuestionIdentity.objects.filter(course=self.course).count(), 1)

    @patch("apps.question_bank.services.course_question_identity._retire_exports")
    def test_snapshot_on_shifted_tail_blocks_whole_delete_even_without_answer(
        self,
        retire_exports,
    ):
        first, second, third = [self.question(index) for index in range(1, 4)]
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="尾部正式实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        QuestionSnapshot.objects.create(
            version=version,
            source_question=third,
            source_sequence=third.sequence,
            code=third.code,
            question_type=third.question_type,
            text=third.text,
            correct_answer="",
            primary_kc="",
        )

        with self.assertRaisesRegex(ReferencedQuestionError, "尾部正式实验"):
            delete_question(first)

        self.assertEqual(Question.objects.filter(bank=self.bank).count(), 3)
        self.assertEqual(
            list(
                CourseQuestionIdentity.objects.filter(course=self.course)
                .order_by("course_sequence")
                .values_list("question_id", "course_sequence")
            ),
            [(first.pk, 1), (second.pk, 2), (third.pk, 3)],
        )
        retire_exports.assert_not_called()

    @patch("apps.question_bank.services.course_question_identity._retire_exports")
    @patch("apps.question_bank.services.course_question_identity._invalidate_draft_previews")
    def test_draft_experiment_reference_is_detached_instead_of_blocking_question_delete(
        self,
        invalidate_previews,
        retire_exports,
    ):
        retire_exports.return_value = {
            "export_count": 0,
            "cleanup_pending": False,
            "cleanup_id": None,
        }
        question = self.question(1)
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="可修订草稿实验",
        )
        ExperimentQuestion.objects.create(
            experiment=experiment,
            question=question,
            display_order=1,
        )

        delete_question(question)

        self.assertTrue(Experiment.objects.filter(pk=experiment.pk).exists())
        self.assertFalse(ExperimentQuestion.objects.filter(experiment=experiment).exists())
        invalidate_previews.assert_called_once_with(
            experiment_ids=[experiment.pk],
            reason="course_question_deleted",
        )

    @patch("apps.question_bank.services.course_question_identity._retire_exports")
    @patch("apps.question_bank.services.course_question_identity._invalidate_draft_previews")
    def test_export_retirement_failure_rolls_back_delete_and_numbering(
        self,
        invalidate_previews,
        retire_exports,
    ):
        first, second = self.question(1), self.question(2)
        retire_exports.side_effect = RuntimeError("export retirement failed")

        with self.assertRaisesRegex(RuntimeError, "export retirement failed"):
            delete_question(first)

        self.assertTrue(Question.objects.filter(pk=first.pk).exists())
        self.assertEqual(
            list(
                CourseQuestionIdentity.objects.filter(course=self.course)
                .order_by("course_sequence")
                .values_list("question_id", "course_sequence")
            ),
            [(first.pk, 1), (second.pk, 2)],
        )
        invalidate_previews.assert_not_called()

    def test_question_delete_retires_real_research_export_snapshot_and_all_its_jobs(self):
        from apps.analytics.models import ExportJob, ExportSnapshot

        first, second = self.question(1), self.question(2)
        affected_snapshot = ExportSnapshot.objects.create(
            course=self.course,
            created_by=self.teacher,
            source_bundle={"tables": {"sessions": "frozen export payload"}},
        )
        research_job = ExportJob.objects.create(
            course=self.course,
            snapshot=affected_snapshot,
            created_by=self.teacher,
            kind=ExportJob.Kind.RESEARCH,
        )
        companion_identity_job = ExportJob.objects.create(
            course=self.course,
            snapshot=affected_snapshot,
            created_by=self.teacher,
            kind=ExportJob.Kind.IDENTITY,
        )
        identity_only_snapshot = ExportSnapshot.objects.create(
            course=self.course,
            created_by=self.teacher,
        )
        unaffected_identity_job = ExportJob.objects.create(
            course=self.course,
            snapshot=identity_only_snapshot,
            created_by=self.teacher,
            kind=ExportJob.Kind.IDENTITY,
        )

        with CaptureQueriesContext(connections["default"]) as queries:
            result = delete_question(first)

        snapshot_reads = [
            query["sql"] for query in queries
            if query["sql"].lstrip().upper().startswith("SELECT")
            and '"analytics_exportsnapshot"' in query["sql"]
        ]
        self.assertTrue(snapshot_reads)
        self.assertFalse(
            any('"source_bundle"' in sql for sql in snapshot_reads),
            "Question deletion must not load frozen export data packages.",
        )

        self.assertEqual(result["retired_export_count"], 2)
        self.assertFalse(ExportSnapshot.objects.filter(pk=affected_snapshot.pk).exists())
        self.assertFalse(
            ExportJob.objects.filter(
                pk__in=[research_job.pk, companion_identity_job.pk]
            ).exists()
        )
        self.assertTrue(ExportSnapshot.objects.filter(pk=identity_only_snapshot.pk).exists())
        self.assertTrue(ExportJob.objects.filter(pk=unaffected_identity_job.pk).exists())
        second.refresh_from_db()
        self.assertEqual(second.course_identity.course_sequence, 1)

    def test_repair_dry_run_then_apply_removes_tombstones_and_preserves_question_pk(self):
        first, second, third = [self.question(index) for index in range(1, 4)]
        first_pk = first.pk
        first.delete()
        stdout = StringIO()

        call_command(
            "repair_course_question_numbers",
            str(self.course.pk),
            stdout=stdout,
        )

        second.refresh_from_db()
        self.assertEqual(second.course_identity.course_sequence, 2)
        self.assertIn('"dry_run": true', stdout.getvalue())
        self.assertIn('"old_course_sequence": 2', stdout.getvalue())
        with patch(
            "apps.question_bank.services.course_question_identity._retire_exports"
        ) as retire_exports:
            retire_exports.return_value = {
                "export_count": 3,
                "cleanup_pending": False,
                "cleanup_id": None,
            }
            stdout = StringIO()
            call_command(
                "repair_course_question_numbers",
                str(self.course.pk),
                "--apply",
                stdout=stdout,
            )

        second.refresh_from_db()
        third.refresh_from_db()
        self.assertEqual(second.pk, first_pk + 1)
        self.assertEqual(second.course_identity.course_sequence, 1)
        self.assertEqual(third.course_identity.course_sequence, 2)
        self.assertFalse(CourseQuestionIdentity.objects.filter(question__isnull=True).exists())
        self.assertIn('"export_count": 3', stdout.getvalue())

    def test_repair_skips_whole_course_when_a_shifted_question_has_snapshot(self):
        first, second = self.question(1), self.question(2)
        first.delete()
        experiment = Experiment.objects.create(
            course=self.course,
            created_by=self.teacher,
            question_bank=self.bank,
            name="阻塞修复的实验",
        )
        version = ExperimentVersion.objects.create(
            experiment=experiment,
            version_number=1,
            published_by=self.teacher,
        )
        QuestionSnapshot.objects.create(
            version=version,
            source_question=second,
            source_sequence=second.sequence,
            code=second.code,
            question_type=second.question_type,
            text=second.text,
            correct_answer=second.correct_answer,
            primary_kc="",
        )
        stdout = StringIO()

        call_command(
            "repair_course_question_numbers",
            str(self.course.pk),
            "--apply",
            stdout=stdout,
        )

        second.refresh_from_db()
        self.assertEqual(second.course_identity.course_sequence, 2)
        self.assertTrue(CourseQuestionIdentity.objects.filter(question__isnull=True).exists())
        self.assertIn('"status": "blocked"', stdout.getvalue())
        self.assertIn("阻塞修复的实验", stdout.getvalue())


class ImportPreviewIdentityMapTests(TestCase):
    def test_pending_preview_becomes_stale_when_course_identity_map_changes(self):
        from .models import ImportPreviewBatch
        from .services.preview_batch import ImportPreviewBatchConflict, ImportPreviewBatchService

        teacher = User.objects.create_user(username="preview-numbering", role=User.Role.TEACHER)
        course = Course.objects.create(
            owner=teacher,
            name="预览编号课程",
            code="PREVIEW-NUMBERING",
            term="2026 秋",
        )
        bank = QuestionBank.objects.create(course=course, created_by=teacher, name="预览题库")
        first = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="第一题",
            correct_answer="一",
        )
        batch = ImportPreviewBatchService.create(
            kind=ImportPreviewBatch.Kind.QUESTION_CONTENT,
            course=course,
            bank=bank,
            actor=teacher,
            session_key="preview-session",
            file_bytes=b"file",
            payload={"rows": []},
            summary={},
        )
        CourseQuestionIdentity.objects.filter(question=first).update(
            course_sequence=2,
            stable_code=f"C{course.pk:06d}-Q000002",
        )

        with self.assertRaises(ImportPreviewBatchConflict) as raised:
            ImportPreviewBatchService.consume(
                batch_id=batch.pk,
                kind=ImportPreviewBatch.Kind.QUESTION_CONTENT,
                course=course,
                bank=bank,
                actor=teacher,
                session_key="preview-session",
            )

        self.assertEqual(raised.exception.code, "preview_stale")


class CourseQuestionResearchMappingIntegrationTests(TestCase):
    def test_safe_reindex_is_shared_by_04_05_06_and_question_library_json(self):
        from apps.analytics import test_teacher_data_center_api as fixtures
        from apps.analytics.data_scope import DataScope
        from apps.analytics.research_dataset import read_research_bundle
        from apps.assessment.event_services import append_assessment_event

        fixtures.TeacherDataCenterApiTests.setUp(self)
        source = self.attempt.assignment.snapshot.source_question
        second = Question.objects.create(
            bank=source.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="待删除的未发布题",
            correct_answer="答案",
        )
        third = Question.objects.create(
            bank=source.bank,
            sequence=3,
            question_type=Question.Type.SHORT,
            text="重排后第二题",
            correct_answer="答案",
        )
        append_assessment_event(
            event_type="item_entered",
            event_version=1,
            event_domain="assessment",
            actor_source="server",
            session=self.attempt.session,
            assignment=self.attempt.assignment,
            attempt=self.attempt,
            question_snapshot=self.attempt.assignment.snapshot,
            item_instance=self.attempt.assignment.item_instance,
            payload={"entry_reason": "navigation_click", "from_assignment_id": None},
        )

        delete_question(second)
        scope = DataScope.from_mapping(
            self.course,
            {"experiment_id": self.experiment.pk, "class_id": self.class_group.pk},
        )
        bundle, _now = read_research_bundle(scope)

        self.assertEqual(
            [row[1] for row in bundle["files"]["course_mapping"]],
            ["Q001", "Q002"],
        )
        self.assertEqual(
            [row["题号"] for row in bundle["files"]["question_library"]],
            ["Q001", "Q002"],
        )
        self.assertEqual(
            {row[3] for row in bundle["files"]["responses"]},
            {"Q001"},
        )
        self.assertEqual(
            {row[3] for row in bundle["files"]["behavior_events"]},
            {"Q001"},
        )
        third.refresh_from_db()
        self.assertEqual(third.course_identity.course_sequence, 2)


@skipUnlessDBFeature("has_select_for_update")
class CourseQuestionRenumberingConcurrencyTests(TransactionTestCase):
    reset_sequences = True

    def test_import_waits_for_course_repair_then_allocates_after_compacted_tail(self):
        from apps.question_bank.services.import_parsing import ParsedQuestion
        from apps.question_bank.services.import_service import import_questions

        teacher = User.objects.create_user(
            username="numbering-concurrency",
            role=User.Role.TEACHER,
        )
        course = Course.objects.create(
            owner=teacher,
            name="编号并发课程",
            code="NUMBERING-CONCURRENCY",
            term="2026 秋",
        )
        bank = QuestionBank.objects.create(
            course=course,
            created_by=teacher,
            name="编号并发题库",
        )
        first = Question.objects.create(
            bank=bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="删除后形成旧缺口",
            correct_answer="答案",
        )
        second = Question.objects.create(
            bank=bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="应压缩到一号",
            correct_answer="答案",
        )
        first.delete()
        repair_has_course_lock = Event()
        allow_repair = Event()
        import_is_calling = Event()

        def repair():
            close_old_connections()
            try:
                with transaction.atomic():
                    locked_course = Course.objects.select_for_update().get(pk=course.pk)
                    repair_has_course_lock.set()
                    if not allow_repair.wait(timeout=10):
                        raise AssertionError("未收到继续修复信号")
                    return renumber_course_question_identities(locked_course)
            finally:
                connections.close_all()

        def import_after_repair():
            close_old_connections()
            try:
                local_bank = QuestionBank.objects.select_related("course").get(pk=bank.pk)
                import_is_calling.set()
                return import_questions(
                    local_bank,
                    [
                        ParsedQuestion(
                            3,
                            "并发导入题",
                            {},
                            "答案",
                            Question.Type.SHORT,
                        )
                    ],
                )[0].pk
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            repair_future = executor.submit(repair)
            self.assertTrue(repair_has_course_lock.wait(timeout=10))
            import_future = executor.submit(import_after_repair)
            self.assertTrue(import_is_calling.wait(timeout=10))
            try:
                with self.assertRaises(FutureTimeoutError):
                    import_future.result(timeout=0.25)
            finally:
                allow_repair.set()
            repair_future.result(timeout=10)
            imported_pk = import_future.result(timeout=10)

        second.refresh_from_db()
        imported = Question.objects.get(pk=imported_pk)
        identities = list(
            CourseQuestionIdentity.objects.filter(course=course)
            .order_by("course_sequence")
            .values_list("question_id", "course_sequence", "stable_code")
        )
        self.assertEqual(
            identities,
            [
                (second.pk, 1, f"C{course.pk:06d}-Q000001"),
                (imported.pk, 2, f"C{course.pk:06d}-Q000002"),
            ],
        )
