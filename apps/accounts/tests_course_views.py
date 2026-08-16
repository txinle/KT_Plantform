from django.test import TestCase
from django.urls import reverse
from django.core.files.uploadedfile import SimpleUploadedFile
from io import BytesIO
from openpyxl import Workbook

from apps.audit.models import AuditLog

from .models import Course, CourseTeacher, User


class CourseWorkspaceViewTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(
            username="owner",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.collaborator = User.objects.create_user(
            username="collaborator",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.outsider = User.objects.create_user(
            username="outsider",
            password="password123",
            role=User.Role.TEACHER,
        )
        self.course = Course.objects.create(
            name="高等数学",
            code="MATH101",
            term="2026春",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)

    def test_teacher_home_and_course_list_are_course_first(self):
        self.client.force_login(self.owner)

        home = self.client.get(reverse("home"))
        self.assertRedirects(home, reverse("courses:list"))
        response = self.client.get(reverse("courses:list"))
        self.assertContains(response, "我的课程")
        self.assertContains(response, "高等数学")
        self.assertContains(response, "MATH101")
        self.assertNotContains(response, "KC 库")

    def test_owner_and_collaborator_can_open_course_but_outsider_gets_404(self):
        detail_url = reverse("courses:detail", args=[self.course.pk])
        for user in (self.owner, self.collaborator):
            with self.subTest(user=user.username):
                self.client.force_login(user)
                response = self.client.get(detail_url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "题库")
                self.assertContains(response, "班级")
                self.assertContains(response, "实验")

        self.client.force_login(self.outsider)
        self.assertEqual(self.client.get(detail_url).status_code, 404)

    def test_course_creation_and_archive_are_audited(self):
        self.client.force_login(self.owner)
        response = self.client.post(
            reverse("courses:create"),
            {
                "name": "大学物理",
                "code": "PHYS101",
                "term": "2026春",
                "description": "研究课程",
            },
        )
        created = Course.objects.get(code="PHYS101")
        self.assertRedirects(response, reverse("courses:detail", args=[created.pk]))
        self.assertTrue(AuditLog.objects.filter(action="course.create", object_id=str(created.pk)).exists())

        archive = self.client.post(reverse("courses:archive", args=[created.pk]))
        self.assertRedirects(archive, reverse("courses:detail", args=[created.pk]))
        created.refresh_from_db()
        self.assertEqual(created.status, Course.Status.ARCHIVED)
        self.assertTrue(AuditLog.objects.filter(action="course.archive", object_id=str(created.pk)).exists())

    def test_only_owner_can_manage_course_settings_and_collaborators(self):
        settings_url = reverse("courses:settings", args=[self.course.pk])
        self.client.force_login(self.collaborator)
        self.assertEqual(self.client.get(settings_url).status_code, 403)

        self.client.force_login(self.owner)
        response = self.client.post(
            settings_url,
            {
                "name": self.course.name,
                "code": self.course.code,
                "term": self.course.term,
                "description": "更新说明",
                "teachers": [self.outsider.pk],
            },
        )
        self.assertRedirects(response, settings_url)
        self.course.refresh_from_db()
        self.assertEqual(self.course.description, "更新说明")
        self.assertQuerySetEqual(
            self.course.teacher_links.values_list("teacher", flat=True),
            [self.outsider.pk],
        )

    def test_class_import_reuses_existing_student_account(self):
        from .models import ClassGroup, CourseEnrollment

        class_group = ClassGroup.objects.create(course=self.course, name="一班")
        student = User.objects.create_user(
            username="S200",
            student_number="S200",
            display_name="学生乙",
            role=User.Role.STUDENT,
            password="existing-password",
        )
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["学号", "姓名", "班级"])
        sheet.append(["S200", "学生乙", str(class_group)])
        stream = BytesIO()
        workbook.save(stream)
        upload = SimpleUploadedFile(
            "students.xlsx",
            stream.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.client.force_login(self.owner)
        url = reverse("courses:student_import", args=[self.course.pk, class_group.pk])

        preview = self.client.post(url, {"file": upload})
        self.assertContains(preview, "S200")
        confirmed = self.client.post(url, {"action": "confirm"})

        self.assertRedirects(
            confirmed,
            reverse("courses:class_detail", args=[self.course.pk, class_group.pk]),
        )
        self.assertTrue(
            CourseEnrollment.objects.filter(course=self.course, class_group=class_group, student=student).exists()
        )
        student.refresh_from_db()
        self.assertTrue(student.check_password("existing-password"))

    def test_student_can_move_between_classes_only_inside_course_and_is_audited(self):
        from .models import ClassGroup, CourseEnrollment

        source = ClassGroup.objects.create(course=self.course, name="一班")
        target = ClassGroup.objects.create(course=self.course, name="二班")
        student = User.objects.create_user(
            username="S300",
            student_number="S300",
            role=User.Role.STUDENT,
        )
        enrollment = CourseEnrollment.objects.create(
            course=self.course,
            class_group=source,
            student=student,
        )
        self.client.force_login(self.owner)

        response = self.client.post(
            reverse("courses:move_student", args=[self.course.pk, source.pk, student.pk]),
            {"class_group": target.pk},
        )

        self.assertRedirects(response, reverse("courses:class_detail", args=[self.course.pk, target.pk]))
        enrollment.refresh_from_db()
        self.assertEqual(enrollment.class_group, target)
        self.assertTrue(
            AuditLog.objects.filter(action="student.class_transfer", object_id=str(enrollment.pk)).exists()
        )
