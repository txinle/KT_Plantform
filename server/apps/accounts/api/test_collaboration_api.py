from django.test import TestCase
from apps.accounts.models import Course, CourseTeacher, User
from apps.api.test_support import domain_payload


class CollaborationApiTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='owner', role='teacher')
        self.member = User.objects.create_user(username='member', role='teacher')
        self.course = Course.objects.create(owner=self.owner, name='Test', code='T', term='2026')
        self.url = f'/api/v1/teacher/courses/{self.course.pk}/collaboration/'
        self.client.force_login(self.owner)

    def test_add_permissions_and_stale_token(self):
        token = domain_payload(self.client.get(self.url))['state_token']
        data = dict(action='add', teacher_id=self.member.pk, permissions=['bank'], state_token=token)
        self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 200)
        self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 400)
        self.assertTrue(self.course.can_manage(self.member, capability='bank'))
        self.assertFalse(self.course.can_manage(self.member, capability='export'))

    def test_member_cannot_promote_self_or_update_legacy_settings(self):
        CourseTeacher.objects.create(course=self.course, teacher=self.member, permissions=['bank'])
        self.client.force_login(self.member)
        payload = domain_payload(self.client.get(self.url))
        self.assertFalse(payload['capabilities']['can_manage_members'])
        self.assertEqual(self.client.post(self.url, dict(action='update', teacher_id=self.member.pk, permissions=['export'], state_token=payload['state_token']), content_type='application/json').status_code, 403)
        self.assertEqual(self.client.post(f'/api/v1/teacher/courses/{self.course.pk}/settings/', {}, content_type='application/json').status_code, 403)

    def test_revoke_immediate_and_legacy_default(self):
        link = CourseTeacher.objects.create(course=self.course, teacher=self.member)
        self.assertTrue(self.course.can_manage(self.member, capability='export'))
        token = domain_payload(self.client.get(self.url))['state_token']
        self.assertEqual(self.client.post(self.url, dict(action='remove', teacher_id=self.member.pk, state_token=token), content_type='application/json').status_code, 200)
        self.assertFalse(CourseTeacher.objects.filter(pk=link.pk).exists())
        self.assertFalse(self.course.can_manage(self.member, capability='bank'))

    def test_module_endpoints_reject_ungranted_capabilities(self):
        CourseTeacher.objects.create(course=self.course, teacher=self.member, permissions=['bank'])
        self.client.force_login(self.member)
        base = f'/api/v1/teacher/courses/{self.course.pk}/'
        self.assertEqual(self.client.get(base + 'banks/').status_code, 200)
        for path in ['classes/', 'experiments/', 'data/overview/', 'data/exports/']:
            with self.subTest(path=path):
                self.assertIn(self.client.get(base + path).status_code, (403, 404))
        self.assertIn(self.client.post(base + 'data/export-jobs/', {}, content_type='application/json').status_code, (403, 404))

    def test_basic_settings_save_preserves_existing_restrictions(self):
        CourseTeacher.objects.create(course=self.course, teacher=self.member, permissions=['bank'])
        url = f'/api/v1/teacher/courses/{self.course.pk}/settings/'
        form = domain_payload(self.client.get(url))['form']
        form['name'] = 'Updated'
        self.assertEqual(self.client.post(url, form, content_type='application/json').status_code, 200)
        self.assertEqual(self.course.teacher_links.get().permissions, ['bank'])

    def test_transfer_requires_reason_and_preserves_selected_old_owner(self):
        token = domain_payload(self.client.get(self.url))['state_token']
        data = dict(action='transfer', teacher_id=self.member.pk, state_token=token, keep_previous=True)
        self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 400)
        data['reason'] = '教学安排调整'
        self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 200)
        self.course.refresh_from_db()
        self.assertEqual(self.course.owner_id, self.member.pk)
        self.assertEqual(set(self.course.teacher_links.get(teacher=self.owner).permissions), {'bank', 'class', 'experiment', 'data'})

    def test_export_requires_data_and_blank_search_does_not_list_directory(self):
        self.assertEqual(domain_payload(self.client.get(self.url + 'teachers/'))['items'], [])
        self.assertEqual(len(domain_payload(self.client.get(self.url + 'teachers/?q=member'))['items']), 1)
        token = domain_payload(self.client.get(self.url))['state_token']
        self.assertEqual(self.client.post(self.url, dict(action='add', teacher_id=self.member.pk, state_token=token, permissions=['export']), content_type='application/json').status_code, 400)

    def test_transfer_without_retaining_actor_redirects_and_revokes(self):
        token = domain_payload(self.client.get(self.url))['state_token']
        response = self.client.post(self.url, dict(action='transfer', teacher_id=self.member.pk, state_token=token, keep_previous=False, reason='移交'), content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(domain_payload(response)['redirect_url'], '/teacher/courses/')
        self.assertEqual(self.client.get(self.url).status_code, 404)

    def test_transfer_collision_rolls_back(self):
        Course.objects.create(owner=self.member, name='Other', code=self.course.code, term=self.course.term)
        token = domain_payload(self.client.get(self.url))['state_token']
        response = self.client.post(self.url, dict(action='transfer', teacher_id=self.member.pk, state_token=token, reason='移交'), content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.course.refresh_from_db()
        self.assertEqual(self.course.owner_id, self.owner.pk)
        self.assertFalse(self.course.teacher_links.exists())
