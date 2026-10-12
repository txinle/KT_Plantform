from django.core.exceptions import PermissionDenied
from django.http import Http404
from django.test import TestCase

from .models import Course, CourseTeacher, User


class CourseAccessTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="owner", role=User.Role.TEACHER)
        self.collaborator = User.objects.create_user(username="collaborator", role=User.Role.TEACHER)
        self.outsider = User.objects.create_user(username="outsider", role=User.Role.TEACHER)
        self.admin = User.objects.create_superuser(username="admin", password="x", role=User.Role.ADMIN)
        self.course = Course.objects.create(
            name="课程",
            code="COURSE",
            term="2026春",
            owner=self.owner,
        )
        CourseTeacher.objects.create(course=self.course, teacher=self.collaborator)

    def test_accessible_courses_include_owner_collaborator_and_admin_only(self):
        from .course_access import accessible_courses

        self.assertQuerySetEqual(accessible_courses(self.owner), [self.course])
        self.assertQuerySetEqual(accessible_courses(self.collaborator), [self.course])
        self.assertQuerySetEqual(accessible_courses(self.outsider), [])
        self.assertQuerySetEqual(accessible_courses(self.admin), [self.course])

    def test_owner_only_and_archived_write_guards(self):
        from .course_access import get_accessible_course, require_course_owner, require_writable_course

        self.assertEqual(get_accessible_course(self.collaborator, self.course.pk), self.course)
        with self.assertRaises(Http404):
            get_accessible_course(self.outsider, self.course.pk)
        with self.assertRaises(PermissionDenied):
            require_course_owner(self.course, self.collaborator)

        self.course.status = Course.Status.ARCHIVED
        self.course.save(update_fields=["status"])
        with self.assertRaises(PermissionDenied):
            require_writable_course(self.course)
