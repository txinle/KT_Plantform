import csv
import io
import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import Course, User
from apps.api.test_support import domain_payload
from .models import Concept, ImportPreviewBatch, Question, QuestionBank


HEADERS = ['序号', '题目', '题型', *[f'选项{k}' for k in 'ABCDEF'], '答案', '可接受答案', '解析', '难度',
    '一级知识点', '二级知识点', '三级知识点', '题组编号', '题组形式', '材料标题', '作答说明', '共享材料', '组内顺序', '空位编号', '内容语言', '分值', '大小写敏感']


def upload(rows, headers=HEADERS):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return SimpleUploadedFile('groups.csv', stream.getvalue().encode('utf-8-sig'))


def cloze_rows():
    return [
        {'序号': '999', '题目': '', '题型': '填空题', '答案': 'Paris', '题组编号': 'G1', '题组形式': 'cloze',
         '材料标题': 'Cities', '共享材料': 'I visited {{b01}} and {{b02}}.', '作答说明': 'Complete.', '组内顺序': '1', '空位编号': 'b01',
         '一级知识点': 'KC1', '内容语言': 'en', '分值': '2.50', '大小写敏感': 'true'},
        {'序号': '999', '题目': '', '题型': '填空题', '答案': 'London', '题组编号': 'G1', '组内顺序': '2', '空位编号': 'b02', '一级知识点': 'KC1'},
    ]


class GroupImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='group-import', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='English', code='ENG', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='English bank')
        Concept.objects.create(course=self.course, code='KC1', name='Vocabulary')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/imports/questions/'

    def preview(self, rows, headers=HEADERS):
        response = self.client.post(self.base + 'preview/', {'file': upload(rows, headers)})
        self.assertEqual(response.status_code, 200, response.content)
        return domain_payload(response)

    def confirm(self, preview, **selection):
        return self.client.post(self.base + 'confirm/', json.dumps({'preview_id': preview['preview_id'], **selection}), content_type='application/json')

    def test_cloze_two_pass_preview_and_persistent_whole_group_confirmation(self):
        preview = self.preview(cloze_rows())
        group = preview['preview']['groups'][0]
        self.assertEqual(group['classification'], 'ready')
        self.assertEqual(group['total_max_score'], '3.50')
        self.assertEqual(group['item_count'], 2)
        self.assertEqual(group['material_segments'][1]['item_key'], group['member_row_ids'][0])
        self.assertFalse(self.bank.questions.exists())
        self.assertEqual(ImportPreviewBatch.objects.get(pk=preview['preview_id']).payload['groups'][0]['group_id'], group['group_id'])
        response = self.confirm(preview, selected_row_ids=[], selected_group_ids=[group['group_id']])
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(domain_payload(response)['created'], 2)
        self.assertEqual(self.bank.groups.count(), 1)
        self.assertEqual(list(self.bank.questions.values_list('sequence', flat=True)), [1, 2])
        self.assertEqual(list(self.bank.questions.values_list('status', flat=True)), ['active', 'active'])
        self.assertEqual(list(self.bank.questions.values_list('text_match_case_sensitive', flat=True)), [True, True])
        repeated = self.confirm(preview, selected_row_ids=[], selected_group_ids=[group['group_id']])
        self.assertEqual(domain_payload(repeated), domain_payload(response))
        changed = self.confirm(preview, selected_row_ids=[], selected_group_ids=[])
        self.assertEqual(changed.status_code, 409)

    def test_group_rows_cannot_be_selected_individually(self):
        preview = self.preview(cloze_rows())
        response = self.confirm(preview, selected_row_ids=[preview['preview']['rows'][0]['row_id']])
        self.assertEqual(response.status_code, 400, response.content)
        self.assertFalse(self.bank.questions.exists())

    def test_structural_conflict_is_invalid_and_missing_answer_or_kc_is_draft(self):
        for change in ({'空位编号': 'b01'}, {'共享材料': 'Conflicting {{b01}} and {{b02}}.'}, {'组内顺序': '3'}):
            rows = cloze_rows()
            rows[1].update(change)
            preview = self.preview(rows)
            self.assertEqual(preview['preview']['groups'][0]['classification'], 'invalid')
            self.assertFalse(preview['preview']['confirmable'])
        rows = cloze_rows()
        rows[1]['答案'] = ''
        rows[1]['一级知识点'] = ''
        preview = self.preview(rows)
        self.assertEqual(preview['preview']['groups'][0]['classification'], 'draft')
        self.assertEqual(self.confirm(preview).status_code, 201)
        self.assertEqual(self.bank.groups.count(), 1)

    def test_group_fingerprint_excludes_file_code_but_includes_score_and_case_policy(self):
        first = self.preview(cloze_rows())
        self.assertEqual(self.confirm(first).status_code, 201)
        rows = cloze_rows()
        for row in rows:
            row['题组编号'] = 'OTHER'
        repeat = self.preview(rows)
        self.assertEqual(repeat['preview']['groups'][0]['classification'], 'duplicate')
        rows[0]['分值'] = '3'
        changed = self.preview(rows)
        self.assertEqual(changed['preview']['groups'][0]['classification'], 'ready')
        self.assertEqual(self.confirm(changed).status_code, 201)
        self.assertEqual(self.bank.groups.count(), 2)

    def test_standalone_uses_persistent_batch_and_keeps_old_explicit_number_conflicts(self):
        bank = QuestionBank.objects.create(owner=self.teacher, name='Standalone')
        self.base = f'/api/v1/teacher/banks/{bank.pk}/imports/questions/'
        old_headers = ['序号', '题目', '题型', '答案']
        preview = self.preview([{'序号': '7', '题目': 'City?', '题型': '填空题', '答案': 'Paris'}], old_headers)
        batch = ImportPreviewBatch.objects.get(pk=preview['preview_id'])
        self.assertIsNone(batch.course_id)
        self.assertEqual(batch.bank_id, bank.pk)
        self.assertEqual(self.confirm(preview).status_code, 201)
        self.assertTrue(bank.questions.filter(sequence=7).exists())
        response = self.client.post(self.base + 'preview/', {'file': upload([{'序号': '7', '题目': 'Other?', '题型': '填空题', '答案': 'Paris'}], old_headers)})
        self.assertEqual(response.status_code, 400)
        rows = cloze_rows()
        for row in rows:
            row['一级知识点'] = ''
        group = self.preview(rows)
        self.assertEqual(group['preview']['groups'][0]['classification'], 'draft')
        self.assertEqual(self.confirm(group).status_code, 201)

    def test_new_score_and_case_columns_are_strict(self):
        for field, value in [('分值', 'NaN'), ('分值', '0'), ('分值', '0.001'), ('大小写敏感', 'maybe')]:
            rows = cloze_rows()
            rows[0][field] = value
            preview = self.preview(rows)
            self.assertEqual(preview['preview']['groups'][0]['classification'], 'invalid')
        response = self.client.post(self.base + 'preview/', {'file': upload(cloze_rows(), HEADERS + ['拼错字段'])})
        self.assertEqual(response.status_code, 400)

    def test_duplicate_only_group_confirmation_skips_and_new_standalone_rows_deduplicate(self):
        self.assertEqual(self.confirm(self.preview(cloze_rows())).status_code, 201)
        duplicate = self.preview(cloze_rows())
        response = self.confirm(duplicate)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(domain_payload(response)['skipped'], 2)
        self.bank = QuestionBank.objects.create(owner=self.teacher, name='Standalone')
        self.base = f'/api/v1/teacher/banks/{self.bank.pk}/imports/questions/'
        row = {'序号': '99', '题目': 'City?', '题型': '填空题', '答案': 'Paris', '分值': '2.50'}
        self.assertEqual(self.confirm(self.preview([row])).status_code, 201)
        repeat = self.preview([row])
        self.assertEqual(repeat['preview']['rows'][0]['classification'], 'duplicate')

    def test_group_preview_binding_expiry_and_whole_group_rollback(self):
        from datetime import timedelta
        from unittest.mock import patch
        from django.utils import timezone
        from .services.unified_import import UnifiedQuestionImportService
        preview = self.preview(cloze_rows())
        other_bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Other')
        for session_key, bank in [('wrong-session', self.bank), (self.client.session.session_key, other_bank)]:
            with self.assertRaises(Exception) as raised:
                UnifiedQuestionImportService.confirm(bank=bank, actor=self.teacher, session_key=session_key, preview_id=preview['preview_id'])
            self.assertEqual(raised.exception.code, 'preview_binding_mismatch')
        ImportPreviewBatch.objects.filter(pk=preview['preview_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(preview).status_code, 409)
        preview = self.preview(cloze_rows())
        with patch('apps.question_bank.services.unified_import.QuestionOption.objects.bulk_create', side_effect=RuntimeError('group write failed')):
            with self.assertRaisesMessage(RuntimeError, 'group write failed'):
                UnifiedQuestionImportService.confirm(bank=self.bank, actor=self.teacher, session_key=self.client.session.session_key, preview_id=preview['preview_id'])
        self.assertFalse(self.bank.questions.exists())
        self.assertFalse(self.bank.groups.exists())
        self.assertEqual(ImportPreviewBatch.objects.get(pk=preview['preview_id']).status, 'pending')

    def test_expired_consumed_group_batch_cannot_replay_results(self):
        from datetime import timedelta
        from django.utils import timezone
        preview = self.preview(cloze_rows())
        self.assertEqual(self.confirm(preview).status_code, 201)
        ImportPreviewBatch.objects.filter(pk=preview['preview_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.assertEqual(self.bank.questions.count(), 2)


@skipUnlessDBFeature('has_select_for_update')
class GroupImportConcurrencyTests(TransactionTestCase):
    def test_concurrent_standalone_batches_create_one_complete_group(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from .services.unified_import import UnifiedQuestionImportService
        teacher = User.objects.create_user(username='concurrent-group-import', role='teacher')
        bank = QuestionBank.objects.create(owner=teacher, name='Concurrent groups')
        concept = Concept.objects.create(owner=teacher, name='Concurrent vocabulary')
        rows = cloze_rows()
        for row in rows:
            row['一级知识点'] = f'id:{concept.pk}'
        previews = [UnifiedQuestionImportService.preview(bank=bank, actor=teacher, session_key=f'session-{index}', uploaded=upload(rows)) for index in range(2)]
        barrier = Barrier(2)

        def confirm(index):
            close_old_connections()
            try:
                current = QuestionBank.objects.get(pk=bank.pk)
                barrier.wait(timeout=10)
                return UnifiedQuestionImportService.confirm(bank=current, actor=teacher, session_key=f'session-{index}', preview_id=previews[index]['preview_id'])
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, range(2)))
        self.assertEqual(sorted(result['created'] for result in results), [0, 2])
        self.assertEqual(bank.groups.count(), 1)
        self.assertEqual(bank.questions.count(), 2)
        self.assertEqual(list(bank.questions.values_list('status', flat=True)), ['active', 'active'])
