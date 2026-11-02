from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone
from rest_framework.test import APIClient

from apps.accounts.models import ClassGroup, Course, User
from apps.api.test_support import domain_payload


class AdminChoiceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='choices-admin', role='admin')
        self.teacher = User.objects.create_user(username='choices-teacher', display_name='张老师', role='teacher')
        self.course = Course.objects.create(name='研究方法', code='RM101', term='秋季', owner=self.teacher)
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def get(self, kind, **params):
        return self.client.get(f'/api/v1/manage/choices/{kind}/', params)

    def test_only_active_administrators_may_read_choices(self):
        self.assertEqual(self.get('courses').status_code, 200)
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.get('courses').status_code, 403)
        self.client.force_authenticate(None)
        self.assertIn(self.get('courses').status_code, (401, 403))
        self.admin.is_active = False
        self.admin.save(update_fields=['is_active'])
        self.client.force_authenticate(self.admin)
        self.assertEqual(self.get('courses').status_code, 403)

    def test_courses_are_paginated_and_selected_item_ignores_page_and_search(self):
        Course.objects.bulk_create([Course(name=f'课程{index:02}', code=f'C{index}', term='秋季', owner=self.teacher) for index in range(12)])
        response = self.get('courses')
        self.assertEqual(response.status_code, 200, response.data)
        first = domain_payload(response)
        second = domain_payload(self.get('courses', page=2))
        self.assertEqual((len(first['items']), first['total'], first['page'], first['total_pages']), (10, 13, 1, 2))
        self.assertEqual(len(second['items']), 3)
        self.assertFalse({row['value'] for row in first['items']} & {row['value'] for row in second['items']})
        searched = domain_payload(self.get('courses', q='C11', selected_id=self.course.pk))
        self.assertEqual(searched['total'], 1)
        self.assertEqual(searched['selected']['value'], self.course.pk)
        self.assertEqual(searched['selected']['label'], self.course.name)
        self.assertIn('RM101', searched['selected']['description'])
        beyond = domain_payload(self.get('courses', page=99))
        self.assertEqual((beyond['items'], beyond['page'], beyond['total_pages']), ([], 99, 2))

    def test_course_search_supports_name_code_term_and_owner(self):
        for q in ('研究', 'RM101', '秋季', '张老师'):
            with self.subTest(q=q):
                response = self.get('courses', q=q)
                self.assertEqual(response.status_code, 200, response.data)
                self.assertEqual([row['value'] for row in domain_payload(response)['items']], [self.course.pk])

    def test_archived_and_deleted_courses_cannot_leak_through_selection(self):
        archived = Course.objects.create(name='归档', code='ARCH', term='秋季', owner=self.teacher, status='archived')
        deleted = Course.all_objects.create(name='删除', code='DEL', term='秋季', owner=self.teacher, deleted_at=timezone.now())
        data = domain_payload(self.get('courses', selected_id=archived.pk))
        self.assertEqual(data['total'], 1)
        self.assertNotIn('selected', data)
        data = domain_payload(self.get('courses', include_archived=1, selected_id=archived.pk))
        self.assertEqual(data['total'], 2)
        self.assertEqual(data['selected']['value'], archived.pk)
        data = domain_payload(self.get('courses', include_archived=1, selected_id=deleted.pk, exclude_id=self.course.pk))
        self.assertEqual(data['total'], 1)
        self.assertNotIn('selected', data)

    def test_classes_are_scoped_active_searchable_and_paginated(self):
        groups = [ClassGroup.objects.create(course=self.course, name=f'班级{index:02}', academic_year='2026') for index in range(12)]
        ClassGroup.objects.create(course=self.course, name='停用', is_active=False)
        other = Course.objects.create(name='其他课程', code='OTHER', term='秋季', owner=self.teacher)
        foreign = ClassGroup.objects.create(course=other, name='其他班级')
        data = domain_payload(self.get('classes', course_id=self.course.pk, page=2, selected_id=groups[0].pk))
        self.assertEqual((len(data['items']), data['total'], data['total_pages']), (2, 12, 2))
        self.assertEqual(data['selected']['value'], groups[0].pk)
        data = domain_payload(self.get('classes', course_id=self.course.pk, q='班级11', selected_id=foreign.pk))
        self.assertEqual([row['value'] for row in data['items']], [groups[-1].pk])
        self.assertNotIn('selected', data)
        self.assertEqual(domain_payload(self.get('classes', course_id=self.course.pk, q='2026'))['total'], 12)

    def test_teacher_options_exclude_other_roles_disabled_deleted_and_excluded(self):
        User.objects.create_user(username='inactive', role='teacher', is_active=False)
        User.objects.create_user(username='deleted', role='teacher', deleted_at=timezone.now())
        User.objects.create_user(username='student', role='student')
        data = domain_payload(self.get('teachers'))
        self.assertEqual([row['value'] for row in data['items']], [self.teacher.pk])
        for q in ('张老师', 'choices-teacher'):
            self.assertEqual(domain_payload(self.get('teachers', q=q))['total'], 1)
        data = domain_payload(self.get('teachers', q='no match', selected_id=self.teacher.pk))
        self.assertEqual(data['selected']['value'], self.teacher.pk)
        data = domain_payload(self.get('teachers', exclude_id=self.teacher.pk, selected_id=self.teacher.pk))
        self.assertEqual((data['items'], data['total'], data['total_pages']), ([], 0, 0))
        self.assertNotIn('selected', data)

    def test_invalid_inputs_return_400(self):
        archived = Course.objects.create(name='归档', code='ARCH', term='秋季', owner=self.teacher, status='archived')
        for kind, params in (
            ('unknown', {}), ('courses', {'page': 0}), ('courses', {'page': 'invalid'}),
            ('courses', {'selected_id': -1}), ('courses', {'exclude_id': 'invalid'}),
            ('courses', {'include_archived': 'yes'}), ('teachers', {'include_archived': 1}),
            ('classes', {}), ('classes', {'course_id': 'invalid'}),
            ('classes', {'course_id': archived.pk}), ('classes', {'course_id': 999999}),
        ):
            with self.subTest(kind=kind, params=params):
                response = self.get(kind, **params)
                self.assertEqual(response.status_code, 400, response.data)

    def test_course_options_use_a_bounded_query_without_domain_projections(self):
        Course.objects.bulk_create([Course(name=f'课程{index}', code=f'Q{index}', term='秋季', owner=self.teacher) for index in range(25)])
        with CaptureQueriesContext(connection) as queries:
            response = self.get('courses')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(len(domain_payload(response)['items']), 10)
        self.assertLessEqual(len(queries), 2)
        self.assertTrue(any('LIMIT 10' in query['sql'] for query in queries))
