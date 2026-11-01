from django.test import TestCase
from rest_framework.test import APIClient

from apps.accounts.models import ClassGroup, Course, User
from apps.api.test_support import domain_payload


class AdminCourseNavigationTests(TestCase):
    def test_course_and_class_catalog_page_links_stay_in_management(self):
        admin = User.objects.create_user(username='course-navigation-admin', role='admin')
        course = Course.objects.create(name='导航课程', code='NAV', term='2026', owner=admin)
        group = ClassGroup.objects.create(course=course, owner=admin, name='导航班级')
        client = APIClient()
        client.force_authenticate(admin)
        row = domain_payload(client.get('/api/v1/manage/courses/'))['items'][0]
        self.assertEqual(row['detail_url'], f'/manage/courses/{course.pk}/settings/')
        self.assertEqual(row['settings_url'], row['detail_url'])
        row = domain_payload(client.get('/api/v1/manage/classes/'))['items'][0]
        self.assertEqual(row['detail_url'], f'/manage/courses/?tab=classes&class_id={group.pk}')
