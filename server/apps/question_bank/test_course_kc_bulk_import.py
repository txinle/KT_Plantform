from django.test import TestCase

from apps.accounts.models import Course, User

from .models import Concept
from .services.import_parsing import ParsedCourseKC


class CourseKCBulkImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username="course-kc-bulk", role=User.Role.TEACHER)
        self.course = Course.objects.create(
            owner=self.teacher,
            name="课程 KC 批量导入",
            code="COURSE-KC-BULK",
            term="2026 秋",
        )
        self.existing = Concept.objects.create(course=self.course, code="KC1", name="数学基础")
        self.archived = Concept.objects.create(
            course=self.course,
            code="KC2",
            name="历史节点",
            status=Concept.Status.ARCHIVED,
        )

    def test_preview_classifies_new_unchanged_archived_and_conflicts(self):
        from .services.course_kc_import import preview_course_kcs

        preview = preview_course_kcs(
            course=self.course,
            parsed_rows=[
                ParsedCourseKC(1, "KC1", "数学，基础"),
                ParsedCourseKC(2, "KC2", "历史节点"),
                ParsedCourseKC(3, "KC3", "新增节点"),
                ParsedCourseKC(4, "KC4", "数学基础"),
            ],
        )

        self.assertEqual(
            [row["classification"] for row in preview["rows"]],
            ["unchanged", "archived_unchanged", "new", "conflict"],
        )
        self.assertFalse(preview["confirmable"])
        self.assertEqual(preview["rows"][3]["error_code"], "normalized_name_conflict")

    def test_confirm_uses_shared_service_and_rejects_stale_preview(self):
        from .services.course_kc_import import CourseKCBulkImportError, apply_course_kc_import, preview_course_kcs

        parsed = [ParsedCourseKC(3, "KC3", "新增节点")]
        preview = preview_course_kcs(course=self.course, parsed_rows=parsed)
        result = apply_course_kc_import(course=self.course, payload=preview)
        self.assertEqual(result, {"created": 1, "unchanged": 0})
        self.assertTrue(Concept.objects.filter(course=self.course, code="KC3", name="新增节点").exists())

        stale = preview_course_kcs(
            course=self.course,
            parsed_rows=[ParsedCourseKC(4, "KC4", "并发节点")],
        )
        Concept.objects.create(course=self.course, code="KC4", name="其他名称")
        with self.assertRaises(CourseKCBulkImportError) as raised:
            apply_course_kc_import(course=self.course, payload=stale)
        self.assertEqual(raised.exception.code, "preview_stale")
