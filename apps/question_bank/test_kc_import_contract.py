from django.test import SimpleTestCase, TestCase

from apps.accounts.models import Course, User

from .models import Concept

from .services import import_parsing


class CourseKCImportParsingTests(SimpleTestCase):
    def test_two_column_course_dictionary_uses_sequence_as_kc_code_anchor(self):
        parser = getattr(import_parsing, "parse_course_kc_rows", None)
        self.assertIsNotNone(parser)

        parsed = parser(
            [
                {"序号": "1", "知识点名称": "EDA 基础"},
                {"序号": "86", "知识点名称": "FPGA AI 加速"},
            ]
        )

        self.assertEqual(
            [(row.sequence, row.code, row.name) for row in parsed],
            [(1, "KC1", "EDA 基础"), (86, "KC86", "FPGA AI 加速")],
        )

    def test_course_dictionary_rejects_duplicate_sequence_and_extra_headers(self):
        parser = getattr(import_parsing, "parse_course_kc_rows", None)
        self.assertIsNotNone(parser)

        with self.assertRaisesMessage(ValueError, "表头"):
            parser([{"序号": "1", "知识点名称": "节点", "说明": "不允许"}])
        with self.assertRaisesMessage(ValueError, "序号 1 重复"):
            parser(
                [
                    {"序号": "1", "知识点名称": "节点一"},
                    {"序号": "1", "知识点名称": "节点二"},
                ]
            )


class QuestionKCFourColumnParsingTests(SimpleTestCase):
    def test_four_columns_build_explicit_three_level_tree_without_positional_inference(self):
        parser = getattr(import_parsing, "parse_question_kc_tree_rows", None)
        self.assertIsNotNone(parser)

        parsed = parser(
            [
                {
                    "题目序号": "1",
                    "一级知识点": "KC1",
                    "二级知识点": "KC2；KC5",
                    "三级知识点": "KC2>KC3；KC2>KC4；KC5>KC6",
                }
            ]
        )[0]

        self.assertEqual(parsed.sequence, 1)
        self.assertEqual(parsed.root, "KC1")
        self.assertEqual(parsed.level_two, ["KC2", "KC5"])
        self.assertEqual(
            parsed.level_three,
            [("KC2", "KC3"), ("KC2", "KC4"), ("KC5", "KC6")],
        )

    def test_four_columns_reject_duplicate_question_rows_and_implicit_third_level(self):
        parser = getattr(import_parsing, "parse_question_kc_tree_rows", None)
        self.assertIsNotNone(parser)
        row = {
            "题目序号": "1",
            "一级知识点": "KC1",
            "二级知识点": "KC2",
            "三级知识点": "KC3",
        }

        with self.assertRaisesMessage(ValueError, "二级KC>三级KC"):
            parser([row])
        with self.assertRaisesMessage(ValueError, "题目序号 1 重复"):
            parser(
                [
                    {**row, "三级知识点": "KC2>KC3"},
                    {**row, "三级知识点": "KC2>KC4"},
                ]
            )


class CourseKCReferenceResolutionTests(TestCase):
    def setUp(self):
        owner = User.objects.create_user(username="kc-resolver-owner", role=User.Role.TEACHER)
        self.course = Course.objects.create(
            owner=owner,
            name="KC 解析课程",
            code="KC-RESOLVE",
            term="2026 秋",
        )
        self.math = Concept.objects.create(course=self.course, code="KC1", name="数学基础")
        self.measurement = Concept.objects.create(course=self.course, code="KC2", name="测量与近似")

    def test_code_then_normalized_name_then_unique_nearest_name(self):
        from .services.course_kc import resolve_course_kc_reference

        by_code = resolve_course_kc_reference(course=self.course, token="ｋｃ１")
        by_name = resolve_course_kc_reference(course=self.course, token="数学，基础")
        by_edit = resolve_course_kc_reference(course=self.course, token="测量与近佀")

        self.assertEqual((by_code.concept, by_code.method), (self.math, "kc_code_exact"))
        self.assertEqual((by_name.concept, by_name.method), (self.math, "name_exact"))
        self.assertEqual((by_edit.concept, by_edit.method, by_edit.distance), (self.measurement, "edit_distance", 1))

    def test_archived_unknown_and_equal_distance_candidates_are_rejected(self):
        from .services.course_kc import CourseKCResolutionError, resolve_course_kc_reference

        archived = Concept.objects.create(
            course=self.course,
            code="KC3",
            name="已归档节点",
            status=Concept.Status.ARCHIVED,
        )
        Concept.objects.create(course=self.course, code="KC4", name="代数甲")
        Concept.objects.create(course=self.course, code="KC5", name="代数乙")

        with self.assertRaises(CourseKCResolutionError) as raised:
            resolve_course_kc_reference(course=self.course, token=archived.code)
        self.assertEqual(raised.exception.code, "kc_archived")
        with self.assertRaises(CourseKCResolutionError) as raised:
            resolve_course_kc_reference(course=self.course, token="代数丙")
        self.assertEqual(raised.exception.code, "kc_ambiguous")
        with self.assertRaises(CourseKCResolutionError) as raised:
            resolve_course_kc_reference(course=self.course, token="完全不存在")
        self.assertEqual(raised.exception.code, "kc_not_found")
