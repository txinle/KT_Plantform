from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.test import SimpleTestCase, TestCase

from .services.question_groups import material_segments, validate_group_structure
from .services.unified_import import _parse_content
from .services.import_templates import build_question_group_template
from .services.unified_reader import read_unified_rows


class LatestQuestionContentTests(SimpleTestCase):
    def test_words_group_preserves_shared_vocabulary_and_blank_identity(self):
        group = {'kind': 'words', 'material': 'At {{b01}}.', 'word_bank': ['home', 'school']}
        members = [{'question_type': 'fill_blank', 'group_order': 1, 'blank_id': 'b01', 'item_key': 'item-1', 'answer': 'home'}]
        self.assertEqual(material_segments(group, members)[1]['item_key'], 'item-1')
        with self.assertRaises(ValidationError):
            validate_group_structure({**group, 'word_bank': []}, members)
        with self.assertRaises(ValidationError):
            validate_group_structure(group, [{**members[0], 'answer': 'outside'}])

    def test_writing_types_keep_existing_short_answer_storage(self):
        for kind in ('translation', 'essay'):
            parsed = _parse_content({'题型': kind, '题目': 'Write your answer.', '答案': 'Reference', '解析': 'Rubric', '分值': '20'})
            self.assertEqual(parsed['question_type'], 'short_answer')
            self.assertEqual(parsed['educational_type'], kind)
            self.assertEqual(parsed['max_score'], '20.00')

    def test_complete_templates_cover_all_eleven_types_in_each_format(self):
        for extension in ('xlsx', 'csv', 'docx'):
            rows = read_unified_rows(SimpleUploadedFile(f'questions.{extension}', build_question_group_template(extension)))
            self.assertTrue(all(not row['_errors'] for row in rows))
            self.assertEqual({row.get('题组形式') for row in rows if row.get('题组形式')}, {'reading', 'words', 'cloze'})
            ordinary = [row for row in rows if not row.get('题组编号')]
            self.assertEqual({row['题型'] for row in ordinary}, {'单选题', '多选题', '判断题', '填空题', '简答题', '数值题', '英汉互译', '作文题'})

    def test_latest_word_numbered_labels_keep_group_metadata(self):
        from io import BytesIO
        from docx import Document
        document = Document()
        document.add_paragraph('全题型题目导入模板', style='Title')
        document.add_paragraph('Passage title', style='Heading 1')
        for text in ('序号：1', '题型：选词填空', '标题：Story', '题组编号：W001', '题组形式：words',
                     '共享材料：At {{b01}}.', '组内顺序：1', '空位编号：b01', '词库：home|school', '答案：home'):
            document.add_paragraph(text)
        document.add_paragraph('Second question', style='Heading 1')
        for text in ('序号：2', '题型：简答题', '题目：Explain.', '答案：Reference'):
            document.add_paragraph(text)
        stream = BytesIO()
        document.save(stream)
        rows = read_unified_rows(SimpleUploadedFile('latest.docx', stream.getvalue()))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['共享词库'], 'home|school')
        self.assertEqual(rows[0]['材料标题'], 'Story')
        self.assertFalse(rows[0]['_errors'])


class LatestQuestionPersistenceTests(TestCase):
    def setUp(self):
        from apps.accounts.models import Course, User
        from .models import Concept
        self.teacher = User.objects.create_user(username='latest-types', role='teacher')
        self.course = Course.objects.create(owner=self.teacher, name='English', code='EN-LATEST', term='2026')
        for code in ('KC1', 'KC2', 'KC3', 'KC5', 'KC6'):
            Concept.objects.create(course=self.course, code=code, name=code)

    def test_each_template_preview_commit_and_reimport_preserves_every_content_field(self):
        from .models import QuestionBank
        from .services.unified_import import UnifiedQuestionImportService as service
        for extension in ('xlsx', 'csv', 'docx'):
            with self.subTest(extension=extension):
                bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name=extension)
                from unittest.mock import patch
                from .services.import_templates import question_group_template_rows
                rows = [{**row, '一级知识点': 'KC1'} for row in question_group_template_rows()]
                with patch('apps.question_bank.services.import_templates.question_group_template_rows', return_value=rows):
                    content = build_question_group_template(extension)
                args = dict(bank=bank, actor=self.teacher, session_key='latest-types')
                preview = service.preview(**args, uploaded=SimpleUploadedFile(f'all.{extension}', content))
                self.assertEqual(preview['preview']['summary'], {'ready': 22, 'draft': 0, 'invalid': 0, 'duplicate': 0})
                result = service.confirm(**args, preview_id=preview['preview_id'])
                self.assertEqual(result['created'], 22)
                words = bank.groups.get(kind='words')
                self.assertEqual(words.word_bank, ['condition', 'note', 'table', 'choose', 'share', 'quietly', 'journey'])
                self.assertEqual(list(words.members.values_list('correct_answer', flat=True)), ['condition', 'note', 'table', 'choose', 'share'])
                self.assertEqual(bank.questions.filter(educational_type='translation').first().question_type, 'short_answer')
                self.assertEqual(str(bank.questions.get(educational_type='essay').max_score), '15.00')
                self.assertEqual(bank.questions.filter(educational_type='translation').count(), 2)
                self.assertEqual(bank.questions.get(question_type='numeric').absolute_tolerance, 0)
                self.assertEqual(bank.questions.get(educational_type='essay').title, 'A Useful School Activity')
                self.assertIn('100–150', bank.questions.get(educational_type='essay').instructions)
                duplicate = service.preview(**args, uploaded=SimpleUploadedFile(f'all.{extension}', content))
                self.assertEqual(duplicate['preview']['summary']['duplicate'], 22)

    def test_words_editor_enforces_vocabulary_and_preserves_member_kc_and_weight(self):
        import json
        from .models import QuestionBank
        bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Editor')
        self.client.force_login(self.teacher)
        url = f'/api/v1/teacher/courses/{self.course.pk}/banks/{bank.pk}/groups/'
        payload = {'kind': 'words', 'material': 'We work {{b01}}.', 'word_bank': ['together', 'alone'], 'members': [
            {'question_type': 'fill_blank', 'blank_id': 'b01', 'group_order': 1, 'answer': 'together', 'max_score': '2.50',
             'teacher_difficulty': '0.350', 'kc_mapping': {'root_kc_code': 'KC1', 'nodes': [{'kc_code': 'KC1', 'parent_kc_code': None, 'order': 1}]}},
        ]}
        response = self.client.post(url, json.dumps(payload), content_type='application/json')
        self.assertEqual(response.status_code, 201, response.content)
        member = bank.questions.get()
        self.assertEqual(member.status, 'active')
        self.assertEqual(str(member.max_score), '2.50')
        self.assertEqual(str(member.teacher_difficulty), '0.350')
        self.assertEqual(member.concept_links.get().concept.code, 'KC1')
        collection = self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/banks/{bank.pk}/questions/?complete_only=1')
        from apps.api.test_support import domain_payload
        items = domain_payload(collection)['items']
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]['unit_key'], f'group:{member.group_id}')
        self.assertEqual(items[0]['question_type'], 'words')
        self.assertEqual(items[0]['max_score'], '2.50')
        self.assertEqual(items[0]['member_kc_mappings'][0]['id'], member.pk)
        self.assertEqual(items[0]['member_kc_mappings'][0]['mapping']['root_kc_code'], 'KC1')
        detail = domain_payload(self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/banks/{bank.pk}/'))
        self.assertEqual(detail['unit_summary']['complete_count'], 1)
        self.assertEqual(detail['unit_summary']['types'], [{'value': 'words', 'label': '选词填空', 'count': 1}])
        banks = domain_payload(self.client.get(f'/api/v1/teacher/courses/{self.course.pk}/banks/'))
        self.assertEqual(banks['items'][0]['complete_question_count'], 1)
        payload['word_bank'] = ['outside']
        self.assertEqual(self.client.post(url, json.dumps(payload), content_type='application/json').status_code, 400)
        self.assertEqual(bank.groups.count(), 1)

    def test_writing_edit_persists_subtype_without_changing_answer_contract(self):
        from .forms import QuestionEditForm
        from .models import Question, QuestionBank
        bank = QuestionBank.objects.create(course=self.course, owner=self.teacher, name='Writing')
        question = Question.objects.create(bank=bank, sequence=1, status='draft', question_type='short_answer', text='Write.', correct_answer='Reference')
        form = QuestionEditForm(data={'text': 'Translate this.', 'question_type': 'short_answer', 'educational_type': 'translation',
            'status': 'draft', 'correct_answer_text': 'Reference translation', 'explanation': 'Teacher criterion'}, instance=question, course=self.course)
        self.assertTrue(form.is_valid(), form.errors)
        saved = form.save()
        self.assertEqual(saved.educational_type, 'translation')
        self.assertEqual(saved.question_type, 'short_answer')
        self.assertEqual(saved.correct_answer, 'Reference translation')

    def test_shared_bank_copy_preserves_latest_content_metadata(self):
        from .services.example_groups import ensure_english_group_examples
        from .services.import_service import copy_shared_bank
        source = ensure_english_group_examples(self.teacher)
        source.visibility = 'shared'
        source.save(update_fields=['visibility'])
        copied = copy_shared_bank(source, self.teacher)
        self.assertEqual(copied.groups.get(kind='words').word_bank, source.groups.get(kind='words').word_bank)
        essay = copied.questions.get(educational_type='essay')
        self.assertEqual(essay.title, 'A Useful School Activity')
        self.assertIn('100–150', essay.instructions)
