from django.test import TestCase, override_settings
from django.urls import include, path
from rest_framework.test import APIClient
from apps.accounts.models import User, Course

urlpatterns = [path('api/v1/manage/', include('apps.accounts.api.admin_workspace_urls'))]


@override_settings(ROOT_URLCONF=__name__)
class AdminWorkspaceTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='manager', role='admin', password='test')
        self.teacher = User.objects.create_user(username='teacher', role='teacher', password='test')
        self.client = APIClient()
        self.client.force_authenticate(self.admin)

    def test_teacher_cannot_read_platform_catalog(self):
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get('/api/v1/manage/users/').status_code, 403)

    def test_bank_workspace_uses_admin_urls_and_requires_admin_context(self):
        from apps.question_bank.models import QuestionBank
        from apps.api.test_support import domain_payload
        course = Course.objects.create(name='课程', code='BANK', term='2026', owner=self.teacher)
        bank = QuestionBank.objects.create(name='题库', course=course, owner=self.teacher, created_by=self.teacher)
        standalone = QuestionBank.objects.create(name='独立题库', owner=self.teacher, created_by=self.teacher)
        rows = domain_payload(self.client.get('/api/v1/manage/resources/'))['banks']
        self.assertEqual(next(row for row in rows if row['id'] == bank.pk)['detail_url'], f'/manage/resources/courses/{course.pk}/banks/{bank.pk}/')
        self.assertEqual(next(row for row in rows if row['id'] == standalone.pk)['detail_url'], f'/manage/resources/banks/{standalone.pk}/')
        url = '/api/v1/manage/access/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTrue(domain_payload(response)['allowed'])
        self.client.force_authenticate(self.teacher)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_all_roles_get_fixed_initial_password_without_manual_input(self):
        for role in ('teacher','admin'):
            username=f'initial-{role}'
            response=self.client.post('/api/v1/manage/users/',{'role':role,'username':username,'name':'新账号'},format='json')
            self.assertEqual(response.status_code,201,response.data)
            user=User.objects.get(username=username)
            self.assertTrue(user.check_password('S'+username))
            self.assertTrue(user.initial_password_reminder_pending)
            self.assertFalse(user.must_change_password)

    def test_teacher_reset_reopens_reminder_and_uses_current_login_name(self):
        from django.utils import timezone
        self.teacher.initial_password_reminder_shown_at=timezone.now()
        self.teacher.save()
        response=self.client.post(f'/api/v1/manage/users/{self.teacher.pk}/password/',{'confirmation':'reset'},format='json')
        self.assertEqual(response.status_code,200,response.data)
        self.teacher.refresh_from_db()
        self.assertTrue(self.teacher.check_password('Steacher'))
        self.assertTrue(self.teacher.initial_password_reminder_pending)
        self.assertFalse(self.teacher.must_change_password)

    @override_settings(ROOT_URLCONF='config.urls')
    def test_staff_can_acknowledge_and_voluntarily_change_initial_password(self):
        from rest_framework.test import APIClient
        for role in ('teacher','admin'):
            user=User.objects.create_user(username=f'reminder-{role}',role=role,password=f'Sreminder-{role}',uses_initial_password=True)
            client=APIClient()
            client.force_login(user)
            response=client.post('/api/v1/auth/password/reminder-shown/',{},format='json')
            self.assertEqual(response.status_code,200,response.data)
            user.refresh_from_db()
            self.assertFalse(user.initial_password_reminder_pending)
            self.assertTrue(user.uses_initial_password)
            changed=client.post('/api/v1/auth/password/change/',{'current_password':f'Sreminder-{role}',
                'new_password1':'Personal-safe-2601!','new_password2':'Personal-safe-2601!'},format='json')
            self.assertEqual(changed.status_code,200,changed.data)
            user.refresh_from_db()
            self.assertTrue(user.check_password('Personal-safe-2601!'))
            self.assertFalse(user.uses_initial_password)

    def test_create_teacher_and_no_password_in_audit(self):
        response = self.client.post('/api/v1/manage/users/', {'role':'teacher','username':'new.teacher','name':'新教师','password':'Temp-long-123!'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(User.objects.get(username='new.teacher').check_password('Snew.teacher'))
        from apps.audit.models import AuditLog
        self.assertNotIn('Temp-long', str(list(AuditLog.objects.values('details'))))

    def test_cannot_disable_self(self):
        response = self.client.post(f'/api/v1/manage/users/{self.admin.pk}/status/', {'is_active':False}, format='json')
        self.assertEqual(response.status_code, 400)
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.is_active)

    def test_student_identity_is_not_teacher_username(self):
        response = self.client.post('/api/v1/manage/users/', {'role':'student','student_number':'2026001','name':'学生'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        student = User.objects.get(student_number='2026001')
        self.assertEqual(student.username, '2026001')
        self.assertTrue(student.check_password('S2026001'))

    def test_stale_course_delete_preview_rejected(self):
        course = Course.objects.create(name='课程', code='C', term='2026', owner=self.teacher)
        preview = self.client.post(f'/api/v1/manage/courses/{course.pk}/delete-preview/', {}, format='json').data
        from apps.api.test_support import domain_payload
        if 'data' in preview:
            preview = preview['data']
        course.name = '已修改'
        course.save()
        response = self.client.post(f'/api/v1/manage/courses/{course.pk}/delete/', {'confirmation':'DELETE', 'confirmation_name':'课程', 'confirmation_token':preview['confirmation_token']}, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        self.assertTrue(Course.objects.filter(pk=course.pk).exists())

    def test_course_delete_requires_typed_name_and_recovers_original_receipt(self):
        course = Course.objects.create(name='待删除课程', code='DEL', term='2026', owner=self.teacher)
        from apps.api.test_support import domain_payload
        preview = domain_payload(self.client.post(f'/api/v1/manage/courses/{course.pk}/delete-preview/', {}, format='json'))
        path = f'/api/v1/manage/courses/{course.pk}/delete/'
        body = {'confirmation':'DELETE', 'confirmation_token':preview['confirmation_token']}
        self.assertEqual(self.client.post(path, body, format='json').status_code, 400)
        body['confirmation_name'] = 'Wrong'
        self.assertEqual(self.client.post(path, body, format='json').status_code, 409)
        body['confirmation_name'] = course.name
        response = self.client.post(path, body, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        retry = self.client.post(path, body, format='json')
        self.assertEqual(retry.status_code, 200, retry.data)
        self.assertEqual(domain_payload(response)['operation_id'], domain_payload(retry)['operation_id'])

    def test_catalogs_use_real_records(self):
        course = Course.objects.create(name='真实课程', code='REAL', term='2026', owner=self.teacher)
        for section in ('overview','users','courses','classes','resources','data','audit'):
            response = self.client.get(f'/api/v1/manage/{section}/')
            self.assertEqual(response.status_code, 200, (section, response.data))
        from apps.accounts.admin_workspace import course_row
        self.assertEqual(course_row(course)['name'], '真实课程')

    def test_class_create_attaches_real_owner_and_policy(self):
        course = Course.objects.create(name='课程', code='CLASS', term='2026', owner=self.teacher)
        response = self.client.post('/api/v1/manage/classes/', {'course_id':course.pk,'name':'一班','academic_year':'2026'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        group = course.classes.get()
        self.assertEqual(group.owner_id, self.teacher.pk)
        self.assertIsNotNone(group.student_analytics_policy)

    def test_teacher_cannot_mutate_admin_resources(self):
        self.client.force_authenticate(self.teacher)
        response = self.client.post('/api/v1/manage/users/', {'name':'不允许','username':'blocked','password':'abc12345'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(username='blocked').exists())

    def test_edit_cannot_promote_role(self):
        response = self.client.post(f'/api/v1/manage/users/{self.teacher.pk}/update/', {'name':'改名','role':'admin'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.teacher.refresh_from_db()
        self.assertEqual(self.teacher.role, 'teacher')

    def test_resources_catalog_counts_complete_groups(self):
        from apps.question_bank.models import QuestionBank, QuestionGroup, Question
        course = Course.objects.create(name='资源课程', code='RES', term='2026', owner=self.teacher)
        bank = QuestionBank.objects.create(name='阅读题库', course=course, owner=self.teacher)
        group = QuestionGroup.objects.create(bank=bank, kind='reading', material='Passage')
        for index in (1, 2):
            Question.objects.create(bank=bank, group=group, group_order=index, sequence=index,
                title=f'小题{index}', question_type='short_answer', correct_answer='answer')
        response = self.client.get('/api/v1/manage/resources/')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['banks'][0]['question_count'], 1)
        self.assertEqual(len(response.data['questions']), 2)

    def test_transfer_requires_reason_and_current_token(self):
        from apps.accounts.collaboration import collaboration_token
        course = Course.objects.create(name='移交课程', code='TRANSFER', term='2026', owner=self.teacher)
        target = User.objects.create_user(username='target', role='teacher')
        response = self.client.post(f'/api/v1/manage/courses/{course.pk}/transfer/',
            {'teacher_id': target.pk, 'state_token': collaboration_token(course), 'reason': ''}, format='json')
        self.assertEqual(response.status_code, 400, response.data)
        course.refresh_from_db()
        self.assertEqual(course.owner_id, self.teacher.pk)

    def test_admin_updates_standalone_bank_owned_by_other_teacher(self):
        from apps.question_bank.models import QuestionBank
        bank=QuestionBank.objects.create(name='原题库',owner=self.teacher)
        response=self.client.post(f'/api/v1/manage/resources/banks/{bank.pk}/update/',
            {'name':'新题库','description':'管理员维护','visibility':'shared'},format='json')
        self.assertEqual(response.status_code,200,response.data)
        bank.refresh_from_db()
        self.assertEqual(bank.name,'新题库')
        self.assertEqual(bank.visibility,'shared')
