from django.core.exceptions import ValidationError
from django.test import TestCase

from apps.accounts.models import Course, User

from .models import Concept


class CourseKCNodeTests(TestCase):
    def setUp(self):
        teacher = User.objects.create_user(
            username="kc-node-teacher", role=User.Role.TEACHER
        )
        self.course = Course.objects.create(
            name="知识结构课程",
            code="KC-NODES",
            term="2026 秋",
            owner=teacher,
        )

    def test_course_kc_is_a_flat_stable_identity_node(self):
        node = Concept.objects.create(
            course=self.course, name="代数", description="课程词典节点"
        )
        second = Concept.objects.create(course=self.course, name="几何")

        self.assertEqual(node.code, "KC1")
        self.assertEqual(second.code, "KC2")
        self.assertEqual(node.depth, 1)
        self.assertEqual(node.full_path, "代数")

    def test_course_kc_rejects_a_fixed_parent(self):
        root = Concept.objects.create(course=self.course, name="数学")

        with self.assertRaisesMessage(ValidationError, "不保存固定父子关系"):
            Concept.objects.create(course=self.course, parent=root, name="代数")
