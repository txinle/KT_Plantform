from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import Course, User

from .models import Concept, Question, QuestionBank
from .services.question_kc_mapping import (
    project_question_kc_mapping,
    replace_question_kc_mapping,
)


class QuestionKCMappingV2Tests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(
            username="kc-v2-teacher",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="KC v2 课程",
            code="KCV2",
            term="2026 秋",
            owner=self.teacher,
        )
        self.bank = QuestionBank.objects.create(course=self.course, name="KC v2 题库")
        self.question = Question.objects.create(
            bank=self.bank,
            sequence=1,
            question_type=Question.Type.SHORT,
            text="测试题目",
            correct_answer="答案",
            status=Question.Status.DRAFT,
        )

    def concept(self, name):
        sequence = self.course.concepts.count() + 1
        return Concept.objects.create(
            course=self.course,
            code=f"KC{sequence}",
            name=name,
        )

    def test_course_kc_codes_are_sequential_and_immutable(self):
        first = self.concept("概念一")
        second = self.concept("概念二")

        self.assertEqual(first.code, "KC1")
        self.assertEqual(second.code, "KC2")

        with self.assertRaisesMessage(ValidationError, "KC1、KC2"):
            Concept.objects.create(
                course=self.course,
                code="KC000003",
                name="旧零填充代码",
            )

        first.code = "KC99"
        with self.assertRaisesMessage(ValidationError, "不可修改"):
            first.save(update_fields=["code"])

    def test_course_kc_normalized_name_uniqueness_includes_width_and_punctuation(self):
        Concept.objects.create(course=self.course, code="KC1", name="ＥＤＡ，基础")

        with self.assertRaises(ValidationError):
            Concept.objects.create(course=self.course, code="KC2", name="EDA,基础。")

    def test_replaces_and_projects_a_single_root_three_level_tree(self):
        root = self.concept("根")
        branch = self.concept("二级")
        leaf_a = self.concept("三级甲")
        leaf_b = self.concept("三级乙")

        replace_question_kc_mapping(
            self.question,
            root_kc_code=root.code,
            nodes=[
                {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                {"kc_code": branch.code, "parent_kc_code": root.code, "order": 1},
                {"kc_code": leaf_a.code, "parent_kc_code": branch.code, "order": 1},
                {"kc_code": leaf_b.code, "parent_kc_code": branch.code, "order": 2},
            ],
        )

        mapping = project_question_kc_mapping(self.question)

        self.assertEqual(mapping["schema_version"], "question-kc-tree-v2")
        self.assertEqual(mapping["root_kc_code"], root.code)
        self.assertEqual(
            [(node["kc_code"], node["depth"], node["parent_kc_code"]) for node in mapping["nodes"]],
            [
                (root.code, 1, None),
                (branch.code, 2, root.code),
                (leaf_a.code, 3, branch.code),
                (leaf_b.code, 3, branch.code),
            ],
        )
        self.assertEqual(len(mapping["mapping_hash"]), 64)

    def test_rejects_depth_greater_than_three_without_changing_existing_mapping(self):
        existing = self.concept("原根")
        replace_question_kc_mapping(
            self.question,
            root_kc_code=existing.code,
            nodes=[{"kc_code": existing.code, "parent_kc_code": None, "order": 1}],
        )
        root = self.concept("新根")
        level_two = self.concept("二级")
        level_three = self.concept("三级")
        level_four = self.concept("四级")

        with self.assertRaisesMessage(ValidationError, "最多三级"):
            replace_question_kc_mapping(
                self.question,
                root_kc_code=root.code,
                nodes=[
                    {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                    {"kc_code": level_two.code, "parent_kc_code": root.code, "order": 1},
                    {"kc_code": level_three.code, "parent_kc_code": level_two.code, "order": 1},
                    {"kc_code": level_four.code, "parent_kc_code": level_three.code, "order": 1},
                ],
            )

        self.assertEqual(project_question_kc_mapping(self.question)["root_kc_code"], existing.code)

    def test_rejects_duplicate_multi_parent_and_archived_kcs(self):
        root = self.concept("根")
        other_parent = self.concept("另一父节点")
        child = self.concept("重复子节点")
        archived = self.concept("已归档")
        archived.status = Concept.Status.ARCHIVED
        archived.save(update_fields=["status"])

        with self.assertRaisesMessage(ValidationError, "重复"):
            replace_question_kc_mapping(
                self.question,
                root_kc_code=root.code,
                nodes=[
                    {"kc_code": root.code, "parent_kc_code": None, "order": 1},
                    {"kc_code": other_parent.code, "parent_kc_code": root.code, "order": 1},
                    {"kc_code": child.code, "parent_kc_code": root.code, "order": 2},
                    {"kc_code": child.code, "parent_kc_code": other_parent.code, "order": 1},
                ],
            )

        with self.assertRaisesMessage(ValidationError, "归档"):
            replace_question_kc_mapping(
                self.question,
                root_kc_code=archived.code,
                nodes=[{"kc_code": archived.code, "parent_kc_code": None, "order": 1}],
            )

    def test_new_question_requested_as_active_activates_after_atomic_tree_replace(self):
        question = Question.objects.create(
            bank=self.bank,
            sequence=2,
            question_type=Question.Type.SHORT,
            text="新建后立即可用",
            correct_answer="答案",
            status=Question.Status.ACTIVE,
        )
        root = self.concept("新题根节点")

        replace_question_kc_mapping(
            question,
            root_kc_code=root.code,
            nodes=[{"kc_code": root.code, "parent_kc_code": None, "order": 1}],
        )

        question.refresh_from_db()
        self.assertEqual(question.status, Question.Status.ACTIVE)
