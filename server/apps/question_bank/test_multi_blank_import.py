import csv
import io
import json
from decimal import Decimal

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, SimpleTestCase

from apps.accounts.models import Course, User
from apps.api.test_support import domain_payload
from .models import Concept, QuestionBank
from .services.scoring import score_payload


class MultiBlankImportTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='multi-blank', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='电路', code='MB', term='2026')
        self.bank = QuestionBank.objects.create(course=self.course, created_by=self.teacher, name='电路题库')
        Concept.objects.create(course=self.course, code='KC1', name='功率')
        self.client.force_login(self.teacher)
        self.base = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/imports/questions/'

    def rows(self):
        return [{'题型': '填空题', '题目': 'k=____；功率为____ W。', '答案': '-1/2', '分值': '2', '一级知识点': 'KC1'},
                {'答案': '6', '分值': '1'}]

    def preview(self, rows):
        output = io.StringIO()
        writer = csv.DictWriter(output, fieldnames=['题型', '题目', '答案', '分值', '解析', '一级知识点'])
        writer.writeheader()
        writer.writerows(rows)
        result = self.client.post(self.base + 'preview/', {'file': SimpleUploadedFile('blanks.csv', output.getvalue().encode('utf-8-sig'))})
        self.assertEqual(result.status_code, 200, result.content)
        return domain_payload(result)

    def test_continuations_create_one_group_two_weighted_items_and_retry_is_duplicate(self):
        result = self.preview(self.rows())
        group = result['preview']['groups'][0]
        self.assertEqual(group['kind'], 'fill_blank')
        self.assertEqual(group['item_count'], 2)
        self.assertEqual(group['total_max_score'], '3.00')
        self.assertEqual(group['classification'], 'ready')
        response = self.client.post(self.base + 'confirm/', json.dumps({'preview_id': result['preview_id']}), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        members = list(self.bank.questions.order_by('group_order'))
        self.assertEqual([q.max_score for q in members], [Decimal('2'), Decimal('1')])
        self.assertEqual([q.blank_id for q in members], ['b01', 'b02'])
        self.assertEqual([q.primary_concept.code for q in members], ['KC1', 'KC1'])
        self.assertTrue(all(q.status == 'active' for q in members))
        self.assertEqual(score_payload(members[0].question_type, members[0].correct_answer, '-0.5', max_score=members[0].max_score), (True, Decimal('2')))
        retry = self.preview(self.rows())
        self.assertEqual(retry['preview']['groups'][0]['classification'], 'duplicate')

    def test_mismatched_blank_count_cannot_be_imported(self):
        rows = self.rows()
        rows[0]['题目'] = 'k=____；功率____；电流____。'
        result = self.preview(rows)
        self.assertEqual(result['preview']['summary']['invalid'], 2)

    def test_orphan_continuation_and_continuation_after_choice_are_invalid(self):
        for rows in ([{'答案': '6', '分值': '1'}], [{'题型': '简答题', '题目': '说明原因', '答案': '原因'}, {'答案': '6'}]):
            with self.subTest(rows=rows):
                result = self.preview(rows)
                self.assertEqual(result['preview']['rows'][-1]['classification'], 'invalid')

    def test_single_blank_stays_single_and_new_stem_starts_new_question(self):
        result = self.preview([{'题型': '填空题', '题目': '电流单位____', '答案': '安培', '一级知识点': 'KC1'}, *self.rows()])
        self.assertEqual(len(result['preview']['groups']), 1)
        self.assertEqual(result['preview']['groups'][0]['item_count'], 2)
        self.assertEqual(len(result['preview']['rows']), 3)

    def test_editor_preserves_item_ids_scores_and_numeric_tolerance(self):
        preview = self.preview(self.rows())
        self.client.post(self.base + 'confirm/', json.dumps({'preview_id': preview['preview_id']}), content_type='application/json')
        group = self.bank.groups.get()
        url = f'/api/v1/teacher/courses/{self.course.pk}/banks/{self.bank.pk}/groups/{group.pk}/'
        document = domain_payload(self.client.get(url))
        ids = [member['id'] for member in document['members']]
        document.update(material='k=____；功率____ W。', expected_revision=document['revision'])
        document['members'][0]['absolute_tolerance'] = '0.01'
        saved = self.client.put(url, json.dumps(document), content_type='application/json')
        self.assertEqual(saved.status_code, 200, saved.content)
        members = domain_payload(saved)['members']
        self.assertEqual([member['id'] for member in members], ids)
        self.assertEqual([member['max_score'] for member in members], ['2.00', '1.00'])
        self.assertEqual(Decimal(members[0]['absolute_tolerance']), Decimal('0.01'))
        self.assertEqual(domain_payload(saved)['status'], 'active')
        document.update(expected_revision=domain_payload(saved)['revision'], material='只有一个空____')
        invalid = self.client.put(url, json.dumps(document), content_type='application/json')
        self.assertEqual(invalid.status_code, 400)
        group.refresh_from_db()
        self.assertIn('{{b02}}', group.material)

    def test_blank_separator_does_not_attach_to_another_question(self):
        result = self.preview([self.rows()[0], {}, self.rows()[1]])
        self.assertEqual(result['preview']['summary']['invalid'], 2)

    def test_mixed_text_and_numeric_blanks_keep_their_own_rules(self):
        rows = self.rows()
        rows[0]['答案'] = '能量守恒'
        result = self.preview(rows)
        self.assertEqual([row['question_type'] for row in result['preview']['rows']], ['fill_blank', 'numeric'])


class NumericFractionTests(SimpleTestCase):
    def test_excel_numeric_zero_is_a_real_continuation_answer(self):
        from .services.multi_blank_import import expand_blank_rows
        rows = expand_blank_rows([{'题目': 'a=____;b=____', '题型': '填空题', '答案': 1}, {'答案': 0}])
        self.assertFalse(rows[1].get('_errors'))
        self.assertEqual(rows[1]['空位编号'], 'b02')

    def test_fraction_equivalence_keeps_per_item_weight(self):
        self.assertEqual(score_payload('numeric', '-1/2', '-0.5', max_score='2'), (True, Decimal('2')))
        self.assertEqual(score_payload('numeric', '6', '6/1'), (True, Decimal('1')))
        for value in ('1/0', 'NaN', '1+5', '6 W'):
            self.assertEqual(score_payload('numeric', '6', value), (False, Decimal('0')))


class SimpleBlankTemplateTests(TestCase):
    def test_downloads_round_trip_the_same_two_rows_without_internal_group_columns(self):
        from .services.unified_reader import read_unified_rows
        from .services.multi_blank_import import expand_blank_rows
        teacher = User.objects.create_user(username='blank-template', role='teacher')
        self.client.force_login(teacher)
        for extension in ('xlsx', 'csv', 'docx'):
            with self.subTest(extension=extension):
                response = self.client.get(f'/api/v1/teacher/import-templates/question-groups.{extension}?layout=blanks')
                self.assertEqual(response.status_code, 200)
                content = b''.join(response.streaming_content) if response.streaming else response.content
                rows = read_unified_rows(SimpleUploadedFile(f'blanks.{extension}', content))
                self.assertEqual(len(rows), 2)
                self.assertFalse(rows[1].get('题型'))
                self.assertFalse(rows[1].get('题目'))
                self.assertFalse(any(row.get('题组编号') for row in rows))
                expanded = expand_blank_rows(rows)
                self.assertTrue(all(not row.get('_errors') for row in expanded), expanded)
                self.assertEqual([row['分值'] for row in expanded], ['2', '1'])
                self.assertEqual([row['空位编号'] for row in expanded], ['b01', 'b02'])
