import csv
import io
import json
import uuid
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature

from apps.accounts.models import Course, User
from .models import Concept, ImportPreviewBatch, Question, QuestionBank


HEADERS = ['序号', '题目', '题型', *[f'选项{k}' for k in 'ABCDEF'],
           '答案', '可接受答案', '解析', '难度', '一级知识点', '二级知识点', '三级知识点']


def upload_rows(rows, headers=HEADERS):
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=headers)
    writer.writeheader()
    writer.writerows(rows)
    return SimpleUploadedFile('questions.csv', output.getvalue().encode('utf-8-sig'))


def question_row(**changes):
    return {'序号': '1', '题目': '请解释\n  FPGA 的用途。', '题型': '简答题',
            '答案': '逻辑实现\n  可配置', '解析': '第一行\n第二行', '难度': '0',
            '一级知识点': 'KC1', '二级知识点': 'KC2', '三级知识点': 'KC2>KC3',
            **changes}


class UnifiedQuestionImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='unified-owner', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='导入课程', code='UNIFIED', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, created_by=self.teacher, name='统一题库')
        for index, name in enumerate(['FPGA基础', '设计流程', '逻辑实现'], 1):
            Concept.objects.create(course=self.course, code=f'KC{index}', name=name)
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/imports/questions/'

    def preview(self, rows):
        response = self.client.post(self.base + 'preview/', {'file': upload_rows(rows)})
        self.assertEqual(response.status_code, 200, response.content)
        return response.json()

    def confirm(self, preview, selected_row_ids=None):
        payload = {'preview_id': preview['preview_id']}
        if selected_row_ids is not None:
            payload['selected_row_ids'] = selected_row_ids
        return self.client.post(self.base + 'confirm/', json.dumps(payload),
                                content_type='application/json')

    def test_single_file_binds_content_and_kcs_without_creating_preview_questions(self):
        preview = self.preview([question_row()])
        row = preview['preview']['rows'][0]
        self.assertIn('classification', row)
        self.assertEqual(row['classification'], 'ready')
        self.assertEqual(row['kc_mapping']['nodes'][2]['name'], '逻辑实现')
        self.assertFalse(self.bank.questions.exists())
        batch = ImportPreviewBatch.objects.get(pk=preview['preview_id'])
        persisted = json.dumps(batch.payload, ensure_ascii=False)
        for source_key in ['source_label', '_location', 'questions.csv', '序号']:
            self.assertNotIn(source_key, persisted)
        self.assertEqual(batch.payload['rows'][0]['errors'], [])
        self.assertEqual(batch.payload['rows'][0]['raw']['一级知识点'], 'KC1')
        result = self.confirm(preview)
        self.assertEqual(result.status_code, 201, result.content)
        question = self.bank.questions.get()
        self.assertEqual(question.status, Question.Status.ACTIVE)
        self.assertEqual(question.text, question_row()['题目'])
        self.assertEqual(question.correct_answer, question_row()['答案'])
        self.assertEqual(question.teacher_difficulty, 0)
        self.assertEqual(set(question.concept_links.values_list('concept__code', flat=True)), {'KC1', 'KC2', 'KC3'})

    def test_partial_import_and_full_file_retry_skip_existing_and_retain_stable_identity(self):
        good = question_row()
        bad = question_row(题目='第二题', 一级知识点='KC99')
        preview = self.preview([good, bad, good])
        self.assertIn('summary', preview['preview'])
        self.assertEqual(preview['preview']['summary'], {'ready': 1, 'draft': 1, 'duplicate': 1, 'invalid': 0})
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(response.json()['created'], 2)
        question = self.bank.questions.get(status=Question.Status.ACTIVE)
        draft = self.bank.questions.get(status=Question.Status.DRAFT)
        identity = question.course_identity.stable_code
        repeat = self.confirm(preview)
        self.assertEqual(repeat.json(), response.json())
        fixed = question_row(题目='第二题')
        second = self.preview([good, fixed])
        self.assertEqual(second['preview']['summary'], {'ready': 1, 'draft': 0, 'duplicate': 1, 'invalid': 0})
        repaired = self.confirm(second).json()
        self.assertEqual((repaired['created'], repaired['updated']), (0, 1))
        self.assertEqual(list(self.bank.questions.values_list('sequence', flat=True)), [1, 2])
        draft.refresh_from_db()
        self.assertEqual(draft.status, Question.Status.ACTIVE)
        self.assertEqual(Question.objects.get(pk=question.pk).course_identity.stable_code, identity)

    def test_original_numbers_are_ignored_and_neighbors_keep_their_own_mapping(self):
        first = question_row(序号='999', 题目='甲', 一级知识点='KC1', 二级知识点='', 三级知识点='')
        second = question_row(序号='999', 题目='乙', 一级知识点='KC3', 二级知识点='', 三级知识点='')
        preview = self.preview([first, second])
        self.assertIn('summary', preview['preview'])
        self.assertEqual(self.confirm(preview).json()['created'], 2)
        self.assertEqual([(q.sequence, q.text, q.primary_concept.code) for q in self.bank.questions.all()],
                         [(1, '甲', 'KC1'), (2, '乙', 'KC3')])

    def test_unknown_or_fuzzy_kc_is_draft_and_changed_content_does_not_overwrite(self):
        typo = question_row(一级知识点='FPGA基出')
        preview = self.preview([typo])
        self.assertIn('summary', preview['preview'])
        self.assertEqual(preview['preview']['summary']['draft'], 1)
        self.assertTrue(preview['preview']['confirmable'])
        imported = self.confirm(preview).json()
        self.assertEqual((imported['created'], imported['draft']), (1, 1))
        repaired = self.confirm(self.preview([question_row()])).json()
        self.assertEqual((repaired['created'], repaired['updated']), (0, 1))
        result = self.confirm(self.preview([question_row(解析='新的解释')]))
        self.assertEqual(result.json()['created'], 1)
        self.assertEqual(self.bank.questions.count(), 2)

    def test_confirm_revalidates_kc_and_does_not_partially_write_question(self):
        preview = self.preview([question_row()])
        self.assertIn('summary', preview['preview'])
        Concept.objects.filter(course=self.course, code='KC3').update(status=Concept.Status.ARCHIVED)
        response = self.confirm(preview)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual((response.json()['created'], response.json()['draft']), (1, 1))
        question = self.bank.questions.get()
        self.assertEqual(question.status, Question.Status.DRAFT)
        self.assertFalse(question.concept_links.exists())

    def test_unexpected_write_failure_rolls_back_all_rows_and_batch_consumption(self):
        from .services.question_kc_mapping import replace_question_kc_mapping
        preview = self.preview([question_row(), question_row(题目='后一道写入失败')])
        self.assertIn('summary', preview['preview'])
        calls = 0

        def fail_after_first(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError('write failed')
            return replace_question_kc_mapping(*args, **kwargs)

        with patch('apps.question_bank.services.unified_import.replace_question_kc_mapping', side_effect=fail_after_first):
            with self.assertLogs('django.request', level='ERROR') as logged:
                with self.assertRaisesMessage(RuntimeError, 'write failed'):
                    self.confirm(preview)
        self.assertTrue(any(record.exc_info and isinstance(record.exc_info[1], RuntimeError) for record in logged.records))
        self.assertFalse(self.bank.questions.exists())
        self.assertEqual(ImportPreviewBatch.objects.get(pk=preview['preview_id']).status, 'pending')

    def test_preview_binding_permissions_expiry_and_replay_checks(self):
        from datetime import timedelta
        from django.utils import timezone
        preview = self.preview([question_row()])
        self.assertIn('summary', preview['preview'])
        # A different browser session belonging to the same teacher cannot consume it.
        from django.test import Client
        other = Client()
        other.force_login(self.teacher)
        denied = other.post(self.base + 'confirm/', json.dumps({'preview_id': preview['preview_id']}), content_type='application/json')
        self.assertEqual(denied.status_code, 409)
        ImportPreviewBatch.objects.filter(pk=preview['preview_id']).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.confirm(preview).status_code, 409)
        self.bank.visibility = QuestionBank.Visibility.ARCHIVED
        self.bank.save(update_fields=['visibility'])
        self.assertEqual(self.client.post(self.base + 'preview/', {'file': upload_rows([question_row()])}).status_code, 403)


    def test_downloaded_templates_import_identical_six_question_records(self):
        from django.urls import reverse
        for index in (4, 5, 6):
            Concept.objects.create(course=self.course, code=f'KC{index}', name=f'知识点{index}')
        for extension, route in [('csv', 'question_import_template'), ('xlsx', 'question_import_template_excel'),
                                 ('docx', 'question_import_template_word')]:
            with self.subTest(extension=extension):
                download = self.client.get(reverse(f'question_bank:{route}'))
                raw = b''.join(download.streaming_content) if download.streaming else download.content
                response = self.client.post(self.base + 'preview/', {'file': SimpleUploadedFile(f'template.{extension}', raw)})
                self.assertEqual(response.status_code, 200, response.content)
                preview = response.json()
                self.assertEqual(preview['preview']['summary']['invalid'], 0, preview)
                self.assertEqual(len(preview['preview']['rows']), 6)
                confirmed = self.confirm(preview)
                self.assertEqual(confirmed.json()['created'], 6 if extension == 'csv' else 0)
                self.assertEqual(self.bank.questions.count(), 6)

    def test_excel_zero_values_reordered_alias_header_and_formula_diagnosis(self):
        from openpyxl import Workbook
        workbook = Workbook()
        sheet = workbook.active
        headers = list(reversed(HEADERS))
        sheet.append(['题干' if header == '题目' else header for header in headers])
        row = question_row(题型='数值题', 答案=0, 难度=0)
        sheet.append([row.get(header, '') for header in headers])
        sheet.append([row.get(header, '') if header != '答案' else '=1+1' for header in headers])
        output = io.BytesIO()
        workbook.save(output)
        response = self.client.post(self.base + 'preview/', {'file': SimpleUploadedFile('zero.xlsx', output.getvalue())})
        self.assertEqual(response.status_code, 200, response.content)
        preview = response.json()
        self.assertEqual(preview['preview']['summary'], {'ready': 1, 'draft': 0, 'duplicate': 0, 'invalid': 1})
        self.assertEqual(preview['preview']['rows'][0]['answer'], '0')
        self.assertEqual(self.confirm(preview).json()['created'], 1)

    def test_draft_content_keeps_full_business_fields_for_correction(self):
        preview = self.preview([question_row(题型='单选题', 选项A='甲\n内容', 选项B='乙', 答案='Z')])
        row = preview['preview']['rows'][0]
        self.assertEqual(row['classification'], 'draft')
        self.assertEqual(row['answer'], 'Z')
        self.assertEqual(row['options'][0]['text'], '甲\n内容')
        self.assertEqual(row['explanation'], question_row()['解析'])

    def test_confirm_selected_ready_rows_only_and_caches_that_selection_result(self):
        unselected = question_row(题目='不选择的题目')
        duplicate = question_row(题目='不选择的题目')
        selected = question_row(题目='只导入这道题')
        invalid = question_row(题目='无效题目', 一级知识点='KC99')
        preview = self.preview([unselected, duplicate, selected, invalid])
        rows = preview['preview']['rows']
        self.assertEqual(
            [row['classification'] for row in rows],
            ['ready', 'duplicate', 'ready', 'draft'],
        )

        response = self.confirm(preview, [rows[2]['row_id']])
        self.assertEqual(response.status_code, 201, response.content)
        result = response.json()
        self.assertEqual((result['created'], result['skipped'], result['failed']), (1, 1, 0))
        self.assertEqual(
            [row['status'] for row in result['rows']],
            ['unselected', 'duplicate', 'created', 'unselected'],
        )
        self.assertEqual(
            set(self.bank.questions.values_list('text', flat=True)),
            {'只导入这道题'},
        )

        retry = self.confirm(preview, [rows[0]['row_id']])
        self.assertEqual(retry.status_code, 201, retry.content)
        self.assertEqual(retry.json(), result)
        self.assertEqual(self.bank.questions.count(), 1)

    def test_confirm_rejects_invalid_unknown_or_duplicate_selected_row_ids_without_writes(self):
        ready = question_row()
        preview = self.preview([
            ready,
            ready,
            question_row(题目='预览无效题目', 题型='连线题'),
        ])
        row_id = preview['preview']['rows'][0]['row_id']
        duplicate_row_id = preview['preview']['rows'][1]['row_id']
        invalid_row_id = preview['preview']['rows'][2]['row_id']
        batch = ImportPreviewBatch.objects.get(pk=preview['preview_id'])

        for selected_row_ids in (
            [],
            [row_id, row_id],
            ['not-a-row-id'],
            [str(uuid.uuid4())],
            [duplicate_row_id],
            [invalid_row_id],
        ):
            with self.subTest(selected_row_ids=selected_row_ids):
                response = self.confirm(preview, selected_row_ids)
                self.assertEqual(response.status_code, 400, response.content)
                self.assertIn('selected_row_ids', response.json()['error']['fields'])
                self.assertFalse(self.bank.questions.exists())
                batch.refresh_from_db()
                self.assertEqual(batch.status, ImportPreviewBatch.Status.PENDING)


@skipUnlessDBFeature('has_select_for_update')
class UnifiedImportConcurrencyTests(TransactionTestCase):
    def test_concurrent_batches_append_one_copy_to_same_bank(self):
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        from django.db import close_old_connections, connections
        from .services.unified_import import UnifiedQuestionImportService
        teacher = User.objects.create_user(username='concurrent-import', role='teacher')
        course = Course.objects.create(owner=teacher, name='并发导入', code='CONCURRENT', term='2026')
        bank = QuestionBank.objects.create(course=course, owner=teacher, name='并发题库')
        Concept.objects.create(course=course, code='KC1', name='主知识点')
        previews = [UnifiedQuestionImportService.preview(
            bank=bank, actor=teacher, session_key=f'session-{i}',
            uploaded=upload_rows([question_row(二级知识点='', 三级知识点='')]),
        ) for i in range(2)]
        barrier = Barrier(2)

        def confirm(index):
            close_old_connections()
            try:
                local_bank = QuestionBank.objects.select_related('course').get(pk=bank.pk)
                barrier.wait(timeout=10)
                return UnifiedQuestionImportService.confirm(bank=local_bank, actor=teacher, session_key=f'session-{index}',
                                                           preview_id=previews[index]['preview_id'])
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(confirm, range(2)))
        self.assertEqual(sorted(result['created'] for result in results), [0, 1])
        self.assertEqual(bank.questions.count(), 1)
        self.assertEqual(bank.questions.get().status, 'active')
