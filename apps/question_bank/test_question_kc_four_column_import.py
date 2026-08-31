from django.test import TestCase

from apps.accounts.models import Course, User

from .models import Concept, Question, QuestionBank
from .services.import_parsing import ParsedQuestionKCTree
from .services.question_kc_mapping import project_question_kc_mapping, replace_question_kc_mapping


class QuestionKCFourColumnImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="four-column-owner", role=User.Role.TEACHER)
        self.course = Course.objects.create(
            owner=self.teacher,
            name="四列导入课程",
            code="FOUR-COLUMN",
            term="2026 秋",
        )
        self.bank = QuestionBank.objects.create(
            course=self.course,
            created_by=self.teacher,
            name="四列题库",
        )
        self.concepts = {
            index: Concept.objects.create(
                course=self.course,
                code=f"KC{index}",
                name=name,
            )
            for index, name in {
                1: "数学基础",
                2: "数与运算",
                3: "加法运算",
                4: "测量与近似",
                5: "质数",
            }.items()
        }
        self.first = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="题目一",
            correct_answer="A",
        )
        self.second = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="题目二",
            correct_answer="B",
        )
        replace_question_kc_mapping(
            self.second,
            root_kc_code="KC5",
            nodes=[{"kc_code": "KC5", "parent_kc_code": None, "order": 1}],
        )

    def test_preview_resolves_code_or_name_and_builds_one_canonical_tree(self):
        from .services.question_kc_import import preview_question_kc_trees

        preview = preview_question_kc_trees(
            bank=self.bank,
            parsed_rows=[
                ParsedQuestionKCTree(
                    sequence=1,
                    root="数学，基础",
                    level_two=["KC2"],
                    level_three=[("数与运算", "加法运祘")],
                )
            ],
        )

        tree = preview["trees"][0]
        self.assertEqual(tree["root_kc_code"], "KC1")
        self.assertEqual(
            tree["nodes"],
            [
                {"kc_code": "KC1", "parent_kc_code": None, "order": 1},
                {"kc_code": "KC2", "parent_kc_code": "KC1", "order": 1},
                {"kc_code": "KC3", "parent_kc_code": "KC2", "order": 1},
            ],
        )
        self.assertEqual([trace["method"] for trace in tree["match_trace"]], ["name_exact", "kc_code_exact", "name_exact", "edit_distance"])

    def test_preview_rejects_foreign_question_parent_and_duplicate_kc(self):
        from .services.question_kc_import import QuestionKCTreeImportError, preview_question_kc_trees

        cases = [
            ParsedQuestionKCTree(99, "KC1", [], []),
            ParsedQuestionKCTree(1, "KC1", ["KC2"], [("KC4", "KC3")]),
            ParsedQuestionKCTree(1, "KC1", ["KC2"], [("KC2", "KC1")]),
        ]
        expected_codes = ["question_not_in_bank", "third_parent_not_level_two", "kc_repeated"]
        for row, expected_code in zip(cases, expected_codes, strict=True):
            with self.subTest(expected_code=expected_code):
                with self.assertRaises(QuestionKCTreeImportError) as raised:
                    preview_question_kc_trees(bank=self.bank, parsed_rows=[row])
                self.assertEqual(raised.exception.code, expected_code)

    def test_confirm_fully_replaces_listed_question_and_leaves_unlisted_unchanged(self):
        from .services.question_kc_import import apply_question_kc_tree_import, preview_question_kc_trees

        payload = preview_question_kc_trees(
            bank=self.bank,
            parsed_rows=[ParsedQuestionKCTree(1, "KC1", ["KC2"], [("KC2", "KC3")])],
        )
        original_second = project_question_kc_mapping(self.second)

        result = apply_question_kc_tree_import(bank=self.bank, payload=payload)

        self.assertEqual(result["questions_updated"], 1)
        self.assertEqual(project_question_kc_mapping(self.first)["root_kc_code"], "KC1")
        self.assertEqual(project_question_kc_mapping(self.second), original_second)
