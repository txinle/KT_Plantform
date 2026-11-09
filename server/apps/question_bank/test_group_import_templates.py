from io import BytesIO
from zipfile import ZipFile

from docx import Document
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase

from apps.accounts.models import User
from .services.unified_reader import read_unified_rows


def docx_file(document):
    output = BytesIO()
    document.save(output)
    return SimpleUploadedFile('groups.docx', output.getvalue())


class GroupWordReaderTests(SimpleTestCase):
    def document(self):
        document = Document()
        for text in ('【题组开始】G1', '【题组形式】cloze', '【共享材料】At {{b01}}.', '【内容语言】en',
                     '【小题】1', '【空位编号】b01', '【题型】填空题', '【答案】home', '【分值】2.50', '【大小写敏感】true', '【题组结束】'):
            document.add_paragraph(text)
        return document

    def test_explicit_group_member_labels_and_mixed_ordinary_question(self):
        document = self.document()
        document.add_paragraph('第2题')
        document.add_paragraph('【题干】An independent question')
        document.add_paragraph('【题型】填空题 【答案】word')
        rows = read_unified_rows(docx_file(document))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['题组编号'], 'G1')
        self.assertEqual(rows[0]['共享材料'], 'At {{b01}}.')
        self.assertEqual(rows[0]['组内顺序'], '1')
        self.assertEqual(rows[0]['空位编号'], 'b01')
        self.assertEqual(rows[0]['_errors'], [])
        self.assertEqual(rows[1].get('题组编号', ''), '')

    def test_unterminated_group_and_unlabelled_member_boundary_are_rejected(self):
        document = self.document()
        last = document.paragraphs[-1]._element
        last.getparent().remove(last)
        with self.assertRaisesMessage(ValueError, '题组结束'):
            read_unified_rows(docx_file(document))
        document = self.document()
        document.paragraphs[4].text = '第1题'
        with self.assertRaisesMessage(ValueError, '小题'):
            read_unified_rows(docx_file(document))

    def test_unknown_word_table_header_is_not_ignored_when_cells_are_empty(self):
        document = Document()
        table = document.add_table(rows=2, cols=4)
        for cell, value in zip(table.rows[0].cells, ['题目', '题型', '答案', '拼错字段']):
            cell.text = value
        for cell, value in zip(table.rows[1].cells, ['City?', '填空题', 'Paris', '']):
            cell.text = value
        rows = read_unified_rows(docx_file(document))
        self.assertTrue(any('拼错字段' in error for error in rows[0]['_errors']))


class GroupTemplateDownloadTests(TestCase):
    def setUp(self):
        self.teacher = User.objects.create_user(username='group-template', role='teacher')
        self.client.force_login(self.teacher)

    def test_downloads_have_latest_twelve_complete_questions_with_exact_headers(self):
        from .services.unified_reader import GROUP_HEADERS
        for extension in ('xlsx', 'csv', 'docx'):
            with self.subTest(extension=extension):
                response = self.client.get(f'/api/v1/teacher/import-templates/question-groups.{extension}')
                self.assertEqual(response.status_code, 200, response.content)
                content = b''.join(response.streaming_content) if response.streaming else response.content
                from .services.import_templates import build_question_image_examples_zip
                with ZipFile(BytesIO(build_question_image_examples_zip())) as archive:
                    images = [SimpleUploadedFile(name, archive.read(name)) for name in archive.namelist() if name.endswith('.png')]
                rows = read_unified_rows(SimpleUploadedFile(f'groups.{extension}', content),
                                         image_files=images if extension == 'csv' else [])
                self.assertEqual(len(rows), 22)
                self.assertEqual(sum(row['题组编号'] == 'R001' for row in rows), 3)
                self.assertEqual(sum(row['题组编号'] == 'C001' for row in rows), 5)
                self.assertTrue(all(not row['_errors'] for row in rows))
                self.assertEqual(rows[0]['一级知识点'], 'KC1')
                self.assertEqual(rows[0]['二级知识点'], 'KC2')
                self.assertEqual(rows[0]['三级知识点'], 'KC2>KC3')
                if extension != 'docx':
                    self.assertTrue(all(header in rows[0] for header in GROUP_HEADERS))
