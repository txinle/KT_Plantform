import csv
import io

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Course, User

from .models import (
    Concept,
    ImportPreviewBatch,
    Question,
    QuestionBank,
    QuestionConcept,
)
from .services.import_parsing import parse_question_rows
from .services.import_service import copy_shared_bank, import_questions


HEADERS = [
    "序号",
    "题目",
    "题型",
    *[f"选项{key}" for key in "ABCDEF"],
    "答案",
    "可接受答案",
    "解析",
    "难度",
    "一级知识点",
    "二级知识点",
    "三级知识点",
]


def upload_rows(rows):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    return SimpleUploadedFile(
        "question-readiness.csv",
        output.getvalue().encode("utf-8-sig"),
    )


def question_row(**changes):
    return {
        "序号": "source-1",
        "题目": "题干",
        "题型": "简答题",
        "答案": "答案",
        "一级知识点": "KC1",
        **changes,
    }


class QuestionImportParserReadinessTests(SimpleTestCase):
    def test_lenient_mode_preserves_correctable_content_while_strict_mode_rejects_it(self):
        row = {
            "序号": 1,
            "题目": "",
            "题型": "数值题",
            "答案": "not-a-number",
            "一级知识点": "missing-kc",
        }

        with self.assertRaisesRegex(ValueError, "缺少题目"):
            parse_question_rows([row])

        parsed = parse_question_rows([row], allow_incomplete=True)[0]
        self.assertEqual(parsed.text, "")
        self.assertEqual(parsed.answer, "not-a-number")
        self.assertEqual(parsed.question_type, Question.Type.NUMERIC)
        self.assertEqual(parsed.raw_primary_kc, "missing-kc")

    def test_lenient_mode_keeps_invalid_choice_and_boolean_answers_as_json_values(self):
        choice, boolean = parse_question_rows(
            [
                {
                    "序号": 1,
                    "题目": "选择",
                    "题型": "单选题",
                    "选项A": "甲",
                    "答案": "Z",
                },
                {
                    "序号": 2,
                    "题目": "判断",
                    "题型": "判断题",
                    "答案": "也许",
                },
            ],
            allow_incomplete=True,
        )

        self.assertEqual(choice.answer, "Z")
        self.assertEqual(boolean.answer, "也许")

    def test_zero_and_false_are_not_cleaned_as_missing(self):
        numeric, boolean = parse_question_rows(
            [
                {"序号": 1, "题目": 0, "题型": "数值题", "答案": 0},
                {"序号": 2, "题目": "判断", "题型": "判断题", "答案": False},
            ]
        )

        self.assertEqual((numeric.text, numeric.answer), ("0", "0"))
        self.assertIs(boolean.answer, False)

    def test_lenient_mode_still_rejects_unknown_types_and_malformed_typed_metadata(self):
        for changes, message in (
            ({"题型": "连线题"}, "未知题型"),
            ({"难度": "困难"}, "难度必须"),
        ):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, message):
                parse_question_rows(
                    [question_row(序号="1", **changes)],
                    allow_incomplete=True,
                )

        with self.assertRaisesRegex(ValueError, "无法根据空答案推断题型"):
            parse_question_rows(
                [{"序号": 1, "题目": "待补", "题型": "", "答案": ""}],
                allow_incomplete=True,
            )


class QuestionImportReadinessTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="question-import-readiness",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            owner=self.teacher,
            name="导入就绪课程",
            code="IMPORT-READY",
            term="2026",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            owner=self.teacher,
            created_by=self.teacher,
            name="导入就绪题库",
        )
        Concept.objects.create(course=self.course, code="KC1", name="知识点一")
        self.client.force_login(self.teacher)
        self.base_url = (
            f"/api/v1/teacher/courses/{self.course.pk}/banks/"
            f"{self.bank.pk}/imports/questions/"
        )

    def preview(self, rows):
        response = self.client.post(
            self.base_url + "preview/",
            {"file": upload_rows(rows)},
        )
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def test_preview_distinguishes_drafts_from_invalid_rows_and_preserves_raw_input(self):
        preview = self.preview(
            [
                question_row(序号="missing-answer", 答案=""),
                question_row(序号="missing-kc", 题目="KC 待修正", 一级知识点="KC99"),
                question_row(序号="unknown-type", 题目="类型错误", 题型="连线题"),
            ]
        )

        self.assertEqual(
            preview["preview"]["summary"],
            {"ready": 0, "draft": 2, "duplicate": 0, "invalid": 1},
        )
        self.assertTrue(preview["preview"]["confirmable"])
        rows = preview["preview"]["rows"]
        self.assertEqual(
            [row["classification"] for row in rows],
            ["draft", "draft", "invalid"],
        )
        self.assertTrue(any("答案" in error for error in rows[0]["errors"]))
        self.assertTrue(any("KC" in error or "知识点" in error for error in rows[1]["errors"]))

        batch = ImportPreviewBatch.objects.get(pk=preview["preview_id"])
        self.assertEqual(batch.payload["rows"][1]["raw"]["一级知识点"], "KC99")
        self.assertTrue(batch.payload["rows"][0]["errors"])

    def test_confirm_saves_ready_and_draft_rows_with_explicit_status_results(self):
        preview = self.preview(
            [
                question_row(序号="ready"),
                question_row(序号="draft", 题目="答案待补", 答案=""),
            ]
        )
        row_ids = [row["row_id"] for row in preview["preview"]["rows"]]
        response = self.client.post(
            self.base_url + "confirm/",
            {"preview_id": preview["preview_id"], "selected_row_ids": row_ids},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual(
            {key: result[key] for key in ("created", "active", "draft", "failed")},
            {"created": 2, "active": 1, "draft": 1, "failed": 0},
        )
        created = [row for row in result["rows"] if row["status"] == "created"]
        self.assertEqual(
            [row["question_status"] for row in created],
            [Question.Status.ACTIVE, Question.Status.DRAFT],
        )
        self.assertEqual(created[0]["draft_reasons"], [])
        self.assertTrue(any("答案" in reason for reason in created[1]["draft_reasons"]))
        self.assertEqual(
            list(self.bank.questions.values_list("status", flat=True)),
            [Question.Status.ACTIVE, Question.Status.DRAFT],
        )

    def test_confirm_revalidation_saves_a_draft_without_persisting_stale_mapping(self):
        preview = self.preview([question_row()])
        Concept.objects.filter(course=self.course, code="KC1").update(
            status=Concept.Status.ARCHIVED
        )

        response = self.client.post(
            self.base_url + "confirm/",
            {"preview_id": preview["preview_id"]},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual((result["created"], result["draft"], result["failed"]), (1, 1, 0))
        question = self.bank.questions.get()
        self.assertEqual(question.status, Question.Status.DRAFT)
        self.assertFalse(question.concept_links.exists())
        self.assertTrue(any("归档" in reason or "KC" in reason for reason in result["rows"][0]["draft_reasons"]))

    def test_incomplete_numeric_rows_deduplicate_without_decimal_or_mapping_crashes(self):
        row = question_row(
            序号="raw-numeric",
            题型="数值题",
            答案="not-a-number",
            一级知识点="",
        )
        preview = self.preview([row, row])
        self.assertEqual(
            [item["classification"] for item in preview["preview"]["rows"]],
            ["draft", "duplicate"],
        )
        first = self.client.post(
            self.base_url + "confirm/",
            {"preview_id": preview["preview_id"]},
            content_type="application/json",
        )
        self.assertEqual(first.status_code, 201, first.content)
        self.assertEqual((first.json()["created"], first.json()["draft"]), (1, 1))

        retried_file = self.preview([row])
        self.assertEqual(
            retried_file["preview"]["rows"][0]["classification"],
            "draft",
        )

    def test_identical_content_with_different_unresolved_kcs_creates_two_drafts(self):
        preview = self.preview(
            [
                question_row(序号="missing-a", 一级知识点="KC-MISSING-A"),
                question_row(序号="missing-b", 一级知识点="KC-MISSING-B"),
            ]
        )

        self.assertEqual(
            [row["classification"] for row in preview["preview"]["rows"]],
            ["draft", "draft"],
        )
        response = self.client.post(
            self.base_url + "confirm/",
            {"preview_id": preview["preview_id"]},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(
            {key: response.json()[key] for key in ("created", "draft", "skipped")},
            {"created": 2, "draft": 2, "skipped": 0},
        )
        self.assertEqual(self.bank.questions.filter(status=Question.Status.DRAFT).count(), 2)

    def test_legacy_import_activates_complete_standalone_and_resolved_course_rows(self):
        standalone = QuestionBank.objects.create(
            owner=self.teacher,
            created_by=self.teacher,
            name="独立题库",
        )
        standalone_question = import_questions(
            standalone,
            parse_question_rows([question_row(序号="1", 一级知识点="")]),
        )[0]
        course_question = import_questions(
            self.bank,
            parse_question_rows([question_row(序号="1", 一级知识点="KC1")]),
        )[0]

        standalone_question.refresh_from_db()
        course_question.refresh_from_db()
        self.assertEqual(standalone_question.status, Question.Status.ACTIVE)
        self.assertEqual(course_question.status, Question.Status.ACTIVE)
        self.assertEqual(course_question.primary_concept.code, "KC1")

        missing_mapping = import_questions(
            self.bank,
            parse_question_rows(
                [question_row(序号="2", 题目="待映射", 一级知识点="")]
            ),
        )[0]
        missing_mapping.refresh_from_db()
        self.assertEqual(missing_mapping.status, Question.Status.DRAFT)

    def test_copy_rechecks_drafts_without_unarchiving_archived_questions(self):
        self.bank.visibility = QuestionBank.Visibility.SHARED
        self.bank.save(update_fields=["visibility"])
        concept = Concept.objects.get(course=self.course, code="KC1")
        for sequence, status in (
            (1, Question.Status.DRAFT),
            (2, Question.Status.ARCHIVED),
        ):
            question = Question.objects.create(
                bank=self.bank,
                sequence=sequence,
                text=f"复制题目 {sequence}",
                question_type=Question.Type.SHORT,
                correct_answer="答案",
                status=status,
            )
            QuestionConcept.objects.create(
                question=question,
                concept=concept,
                is_primary=True,
            )
        target_course = Course.objects.create(
            owner=self.teacher,
            name="复制目标课程",
            code="IMPORT-COPY",
            term="2026",
        )

        copied = copy_shared_bank(self.bank, target_course, self.teacher)

        self.assertEqual(
            list(copied.questions.values_list("status", flat=True)),
            [Question.Status.ACTIVE, Question.Status.ARCHIVED],
        )
