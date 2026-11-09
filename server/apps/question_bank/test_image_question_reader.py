import csv
import hashlib
from io import BytesIO, StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase
from docx import Document
from docx.oxml.ns import qn
from openpyxl import Workbook
from openpyxl.drawing.image import Image as ExcelImage
from PIL import Image
from zipfile import ZipFile

from .services.unified_reader import GROUP_HEADERS, HEADERS, read_unified_rows


def picture(color='red', format='PNG'):
    output = BytesIO()
    Image.new('RGB', (24, 12), color).save(output, format=format)
    return output.getvalue()


def uploaded_document(document):
    output = BytesIO()
    document.save(output)
    return SimpleUploadedFile('questions.docx', output.getvalue())


def marker(data, extension='png'):
    return '{{image:' + hashlib.sha256(data).hexdigest() + '.' + extension + '}}'


class ImageQuestionReaderTests(SimpleTestCase):
    def test_excel_unreadable_embedded_image_is_not_silently_dropped(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(['1', 'text', '简答题'])
        sheet.add_image(ExcelImage(BytesIO(picture())), 'B2')
        source, output = BytesIO(), BytesIO()
        workbook.save(source)
        with ZipFile(source) as original, ZipFile(output, 'w') as archive:
            for name in original.namelist():
                archive.writestr(name, b'bad' if name == 'xl/media/image1.png' else original.read(name))
        with self.assertRaisesMessage(ValueError, '图片'):
            read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))

    def test_excel_incell_and_wps_image_storage_are_explicitly_rejected(self):
        for partname in ('xl/richData/richValueRel.xml', 'xl/cellimages.xml'):
            with self.subTest(partname=partname):
                workbook = Workbook()
                sheet = workbook.active
                sheet.append(HEADERS)
                sheet.append(['1', 'text', '简答题'])
                output = BytesIO()
                workbook.save(output)
                with ZipFile(output, 'a') as archive:
                    archive.writestr(partname, '<unsupported/>')
                with self.assertRaisesMessage(ValueError, '锚定图片'):
                    read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))

    def test_image_references_in_noncontent_fields_are_row_errors(self):
        for field in ('材料标题', '答案', '一级知识点', '作答说明'):
            with self.subTest(field=field):
                output = StringIO()
                writer = csv.DictWriter(output, fieldnames=GROUP_HEADERS)
                writer.writeheader()
                writer.writerow({'序号': '1', '题目': 'text', '题型': '简答题',
                                 field: '{{image:circuit.png}}'})
                rows = read_unified_rows(SimpleUploadedFile('questions.csv', output.getvalue().encode()),
                                         image_files=[SimpleUploadedFile('circuit.png', picture())])
                self.assertTrue(any(field in error for error in rows[0]['_errors']))
                self.assertNotIn('_image_assets', rows[0])

    def test_excel_filename_reference_uses_attachment_and_same_field_contract_as_csv(self):
        data = picture()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(['1', '{{image:circuit.png}}', '单选题', '{{image:circuit.png}}', 'text', '', '', '', '', 'A'])
        output = BytesIO()
        workbook.save(output)
        rows = read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()),
                                 image_files=[SimpleUploadedFile('circuit.png', data)])
        self.assertEqual(rows[0]['题目'], marker(data))
        self.assertEqual(rows[0]['选项A'], marker(data))
        self.assertEqual(rows[0]['_errors'], [])

    def test_excel_picture_on_empty_noncontent_row_is_not_silently_dropped(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(['1', 'text', '简答题'])
        sheet.add_image(ExcelImage(BytesIO(picture())), 'J3')
        output = BytesIO()
        workbook.save(output)
        rows = read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))
        self.assertEqual(len(rows), 2)
        self.assertTrue(any('图片' in error for error in rows[1]['_errors']))

    def test_word_jpeg_and_webp_are_supported_inline(self):
        for format, extension in [('JPEG', 'jpg'), ('WEBP', 'webp')]:
            with self.subTest(format=format):
                data = picture(format=format)
                document = Document()
                document.add_paragraph('第1题')
                # python-docx cannot author WebP drawings, so replace its PNG part.
                run = document.add_paragraph('【题干】').add_run()
                run.add_picture(BytesIO(picture()))
                blip = next(run._r.iter(qn('a:blip')))
                document.part.rels[blip.get(qn('r:embed'))].target_part._blob = data
                rows = read_unified_rows(uploaded_document(document))
                self.assertEqual(rows[0]['题目'], marker(data, extension))
                self.assertEqual(rows[0]['_errors'], [])

    def test_excel_formula_with_picture_is_still_rejected(self):
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(['1', '=1+1', '简答题'])
        sheet.add_image(ExcelImage(BytesIO(picture())), 'B2')
        output = BytesIO()
        workbook.save(output)
        row = read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))[0]
        self.assertTrue(any('公式' in error for error in row['_errors']))

    def test_invalid_attachment_and_duplicate_filenames_are_row_errors(self):
        content = ','.join(HEADERS) + '\n1,{{image:circuit.png}},简答题\n'
        for attachments in ([SimpleUploadedFile('circuit.png', b'bad')],
                            [SimpleUploadedFile('circuit.png', picture()),
                             SimpleUploadedFile('circuit.png', picture('blue'))]):
            with self.subTest(attachments=len(attachments)):
                rows = read_unified_rows(SimpleUploadedFile('questions.csv', content.encode()),
                                         image_files=attachments)
                self.assertTrue(rows[0]['_errors'])
                self.assertNotIn('_image_assets', rows[0])

    def test_word_inline_images_keep_position_and_pure_image_options(self):
        data = picture()
        document = Document()
        document.add_paragraph('第1题')
        stem = document.add_paragraph('【题干】before ')
        stem.add_run().add_picture(BytesIO(data))
        stem.add_run(' after')
        document.add_paragraph('【题型】单选题')
        option = document.add_paragraph('【选项A】')
        option.add_run().add_picture(BytesIO(data))
        document.add_paragraph('【选项B】text 【答案】A')
        rows = read_unified_rows(uploaded_document(document))
        self.assertEqual(rows[0]['题目'], 'before ' + marker(data) + ' after')
        self.assertEqual(rows[0]['选项A'], marker(data))
        self.assertEqual(rows[0]['答案'], 'A')
        self.assertEqual(rows[0]['_errors'], [])
        self.assertEqual(set(rows[0]['_image_assets']), {marker(data)[8:-2]})

    def test_word_group_shared_picture_and_explanation_are_associated_with_members(self):
        shared, explanation = picture(), picture('blue')
        document = Document()
        document.add_paragraph('【题组开始】R1')
        document.add_paragraph('【题组形式】reading')
        material = document.add_paragraph('【共享材料】diagram ')
        material.add_run().add_picture(BytesIO(shared))
        for number in (1, 2):
            document.add_paragraph(f'【小题】{number}')
            document.add_paragraph('【题干】Which? 【题型】单选题 【选项A】one 【选项B】two 【答案】A')
            if number == 1:
                document.add_paragraph('【解析】').add_run().add_picture(BytesIO(explanation))
        document.add_paragraph('【题组结束】')
        rows = read_unified_rows(uploaded_document(document))
        self.assertEqual(rows[0]['共享材料'], 'diagram ' + marker(shared))
        self.assertEqual(rows[1]['共享材料'], rows[0]['共享材料'])
        self.assertEqual(len(rows[0]['_image_assets']), 2)
        self.assertEqual(len(rows[1]['_image_assets']), 1)
        self.assertTrue(all(not row['_errors'] for row in rows))

    def test_word_floating_and_external_pictures_are_explicit_errors(self):
        for kind, expected in [('floating', '浮动'), ('external', '外部')]:
            with self.subTest(kind=kind):
                document = Document()
                document.add_paragraph('第1题')
                run = document.add_paragraph('【题干】question').add_run()
                run.add_picture(BytesIO(picture()))
                drawing = run._r.find(qn('w:drawing'))
                if kind == 'floating':
                    drawing[0].tag = qn('wp:anchor')
                else:
                    blip = next(drawing.iter(qn('a:blip')))
                    blip.set(qn('r:link'), 'https://example.com/image.png')
                    blip.attrib.pop(qn('r:embed'))
                rows = read_unified_rows(uploaded_document(document))
                self.assertTrue(any(expected in error for error in rows[0]['_errors']))

    def test_excel_embedded_images_map_to_their_content_cell(self):
        data = picture()
        workbook = Workbook()
        sheet = workbook.active
        sheet.append(HEADERS)
        sheet.append(['1', 'before', '单选题', '', 'text', '', '', '', '', 'A'])
        sheet.add_image(ExcelImage(BytesIO(data)), 'B2')
        sheet.add_image(ExcelImage(BytesIO(data)), 'D2')
        output = BytesIO()
        workbook.save(output)
        rows = read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))
        self.assertEqual(rows[0]['题目'], 'before\n' + marker(data))
        self.assertEqual(rows[0]['选项A'], marker(data))
        self.assertEqual(rows[0]['选项B'], 'text')
        self.assertEqual(rows[0]['_errors'], [])

    def test_excel_pictures_preserve_reordered_and_whitespace_header_alias_contract(self):
        from openpyxl.utils import get_column_letter
        data = picture()
        headers = list(reversed(HEADERS))
        workbook = Workbook()
        sheet = workbook.active
        sheet.append([' 题干 ' if header == '题目' else header for header in headers])
        values = {'序号': '1', '题目': 'stem', '题型': '简答题'}
        sheet.append([values.get(header, '') for header in headers])
        sheet.add_image(ExcelImage(BytesIO(data)), f'{get_column_letter(headers.index("题目") + 1)}2')
        output = BytesIO()
        workbook.save(output)
        rows = read_unified_rows(SimpleUploadedFile('questions.xlsx', output.getvalue()))
        self.assertEqual(rows[0]['题目'], 'stem\n' + marker(data))
        self.assertEqual(rows[0]['_errors'], [])

    def test_csv_attachment_references_resolve_per_row_and_missing_files_are_errors(self):
        output = StringIO()
        writer = csv.DictWriter(output, fieldnames=HEADERS)
        writer.writeheader()
        writer.writerow({'序号': '1', '题目': 'See {{image:circuit.png}}', '题型': '单选题',
                         '选项A': '{{image:circuit.png}}', '选项B': 'text', '答案': 'A'})
        writer.writerow({'序号': '2', '题目': 'See {{image:missing.png}}', '题型': '简答题'})
        data = picture()
        rows = read_unified_rows(SimpleUploadedFile('questions.csv', output.getvalue().encode()),
                                 image_files=[SimpleUploadedFile('circuit.png', data)])
        self.assertEqual(rows[0]['选项A'], marker(data))
        self.assertEqual(len(rows[0]['_image_assets']), 1)
        self.assertEqual(rows[0]['_errors'], [])
        self.assertTrue(any('missing.png' in error for error in rows[1]['_errors']))
        self.assertNotIn('_image_assets', rows[1])

    def test_text_only_csv_output_retains_its_existing_metadata(self):
        content = ','.join(HEADERS) + '\n1,text,简答题\n'
        row = read_unified_rows(SimpleUploadedFile('questions.csv', content.encode()))[0]
        self.assertNotIn('_image_assets', row)
        self.assertEqual(row['_errors'], [])
        self.assertFalse(row['_legacy_standalone'])


class ImageTemplateRoundtripTests(SimpleTestCase):
    def test_excel_stem_picture_is_below_text_within_its_content_cell(self):
        from .services.import_templates import build_question_group_template
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(build_question_group_template('xlsx')))
        sheet = workbook.active
        stem = next(image for image in sheet._images if image.anchor._from.col == 1)
        self.assertEqual(stem.anchor._from.row, 1)
        self.assertGreater(stem.anchor._from.rowOff, 0)
        self.assertLessEqual(stem.anchor._from.rowOff + stem.anchor.ext.cy,
                             sheet.row_dimensions[2].height * 12700)
        self.assertTrue(all(image.anchor._from.rowOff == 0 for image in sheet._images if image is not stem))

    def test_latest_and_basic_templates_embed_stem_and_pure_option_pictures(self):
        from .services import import_templates as templates
        for family in ('latest', 'basic'):
            actual = {}
            for extension in ('csv', 'xlsx', 'docx'):
                with self.subTest(family=family, extension=extension):
                    if family == 'latest':
                        content = templates.build_question_group_template(extension)
                    else:
                        content = getattr(templates, 'build_question_import_template_' +
                                          {'csv': 'csv', 'xlsx': 'excel', 'docx': 'word'}[extension])()
                    attachments = []
                    if extension == 'csv':
                        with ZipFile(BytesIO(templates.build_question_image_examples_zip())) as archive:
                            attachments = [SimpleUploadedFile(name, archive.read(name))
                                           for name in archive.namelist() if name.endswith('.png')]
                    rows = read_unified_rows(SimpleUploadedFile('template.' + extension, content),
                                             image_files=attachments)
                    self.assertEqual(len(rows), 22 if family == 'latest' else 6)
                    self.assertTrue(all(not row['_errors'] for row in rows), rows[0]['_errors'])
                    self.assertIn('{{image:', rows[0]['题目'])
                    for label in 'ABCD':
                        self.assertRegex(rows[0]['选项' + label], r'^\{\{image:[0-9a-f]{64}\.png\}\}$')
                    self.assertEqual(len(rows[0]['_image_assets']), 4)
                    self.assertNotIn('CSV', rows[0]['解析'])
                    actual[extension] = {field: rows[0][field] for field in ('题目', '选项A', '选项B', '选项C', '选项D', '答案')}
            self.assertEqual(actual['csv'], actual['xlsx'])
            self.assertEqual(actual['csv'], actual['docx'])

    def test_picture_instructions_explain_csv_joint_attachment_selection(self):
        from .services.import_templates import build_question_group_template
        from openpyxl import load_workbook
        workbook = load_workbook(BytesIO(build_question_group_template('xlsx')))
        instructions = '\n'.join(str(row[0].value or '') for row in workbook['填写说明'])
        self.assertIn('CSV', instructions)
        self.assertIn('同时', instructions)
        self.assertIn('PNG', instructions)
        document = Document(BytesIO(build_question_group_template('docx')))
        self.assertTrue(any('CSV' in paragraph.text and '同时' in paragraph.text for paragraph in document.paragraphs))
