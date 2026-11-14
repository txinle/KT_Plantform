from __future__ import annotations

import csv
from io import BytesIO, StringIO
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Inches, RGBColor
from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.drawing.image import Image as ExcelImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Font
from openpyxl.utils.units import pixels_to_EMU


QUESTION_IMPORT_TEMPLATE_HEADERS = (
    "序号",
    "题目",
    "题型",
    "选项A",
    "选项B",
    "选项C",
    "选项D",
    "选项E",
    "选项F",
    "答案",
    "可接受答案",
    "解析",
    "难度",
    "一级知识点",
    "二级知识点",
    "三级知识点",
)

KC_TEMPLATE_INSTRUCTION = (
    "KC1、KC2、KC3、KC5、KC6 仅为示例，请替换为当前课程中已存在的 "
    "KC 编码；导入不会创建课程 KC。二级知识点用“；”分隔，三级知识点使用“二级KC>三级KC”语法。"
)

QUESTION_IMAGE_EXAMPLES_FILENAME = 'question_import_images.zip'
IMAGE_TEMPLATE_INSTRUCTION = (
    '图片填写说明：Word 使用嵌入型图片；Excel 将图片左上角锚定到对应题干、选项、共享材料或解析单元格。'
    '支持 PNG、JPEG、WebP。CSV 使用双花括号包围 image:文件名.png 的引用格式。'
    '下载 question_import_images.zip 并解压；选择 CSV 时，同时选择解压后的 4 张 PNG 图片附件一起预览。'
    '文件名须与引用一致，不能只上传 ZIP；纯图片选项可直接保留图片，无需补写文字。'
)
_IMAGE_ASSETS = Path(__file__).with_name('template_assets')
_EXAMPLE_IMAGE_REFERENCE = re.compile(r'\{\{image:(image[1-4]\.png)\}\}')


def _image_example_rows(rows):
    rows = [dict(row) for row in rows]
    first = rows[0]
    answer = first['答案']
    filenames = ['image1.png', 'image2.png', 'image4.png']
    for label in 'ABCD':
        filename = 'image3.png' if label == answer else filenames.pop(0)
        first['选项' + label] = '{{image:' + filename + '}}'
    first.update({
        '题目': '题干图展示一个串联电路。以下哪幅选项图使用电压表测量电阻两端的电压？\n{{image:image2.png}}',
        '解析': '正确选项中，电压表与被测电阻并联。',
    })
    if '材料标题' in first:
        first['材料标题'] = '电路图与电压测量'
    return rows


def build_question_image_examples_zip() -> bytes:
    output = BytesIO()
    with ZipFile(output, 'w', compression=ZIP_DEFLATED) as archive:
        for number in range(1, 5):
            filename = f'image{number}.png'
            archive.writestr(filename, (_IMAGE_ASSETS / filename).read_bytes())
        archive.writestr('README.txt', IMAGE_TEMPLATE_INSTRUCTION.encode('utf-8-sig'))
    return output.getvalue()


def _embed_excel_images(sheet):
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            if not isinstance(cell.value, str):
                continue
            filenames = _EXAMPLE_IMAGE_REFERENCE.findall(cell.value)
            if not filenames:
                continue
            cell.value = _EXAMPLE_IMAGE_REFERENCE.sub('', cell.value).strip('\n')
            for filename in filenames:
                image = ExcelImage(str(_IMAGE_ASSETS / filename))
                original_width, original_height = image.width, image.height
                image.width = 330 if cell.column == 2 else 220
                image.height = image.width * original_height / original_width
                text_offset = 44 if cell.value else 0
                image.anchor = OneCellAnchor(
                    _from=AnchorMarker(col=cell.column - 1, row=cell.row - 1, rowOff=pixels_to_EMU(text_offset)),
                    ext=XDRPositiveSize2D(pixels_to_EMU(image.width), pixels_to_EMU(image.height)),
                )
                sheet.add_image(image)
                sheet.row_dimensions[cell.row].height = max(
                    sheet.row_dimensions[cell.row].height or 180, (text_offset + image.height + 16) * 0.75)
            sheet.column_dimensions[cell.column_letter].width = 65 if cell.column == 2 else 36


def _sample_row(*values: str) -> dict[str, str]:
    return dict(zip(QUESTION_IMPORT_TEMPLATE_HEADERS, values, strict=True))


QUESTION_IMPORT_TEMPLATE_ROWS = (
    _sample_row(
        "1",
        "2 + 2 等于多少？\n请从下列选项中选择。",
        "单选题",
        "3",
        "4",
        "5",
        "6",
        "",
        "",
        "B",
        "",
        "4 是唯一正确答案。\n这是基础运算示例。",
        "0.100",
        "KC1",
        "KC2；KC5",
        "KC2>KC3；KC5>KC6",
    ),
    _sample_row(
        "2",
        "下列哪些数是质数？\n可多选。",
        "多选题",
        "2\n（质数）",
        "4\n（合数）",
        "5\n（质数）",
        "6",
        "",
        "",
        "A,C",
        "",
        "2、5 是质数。\n4、6 是合数。",
        "0.350",
        "KC1",
        "KC2；KC5",
        "KC2>KC3；KC5>KC6",
    ),
    _sample_row(
        "3",
        "所有学习者都必须获得完全相同的题目顺序。",
        "判断题",
        "",
        "",
        "",
        "",
        "",
        "",
        "错误",
        "",
        "题目顺序可依据实验设置随机化。",
        "0.200",
        "KC1",
        "KC5",
        "KC5>KC6",
    ),
    _sample_row(
        "4",
        "Knowledge Tracing 的中文名称是？",
        "填空题",
        "",
        "",
        "",
        "",
        "",
        "",
        "知识追踪",
        "知识跟踪；KT",
        "常译为知识追踪。",
        "0.450",
        "KC1",
        "KC2",
        "KC2>KC3",
    ),
    _sample_row(
        "5",
        "请简述知识追踪的研究目的。\n请使用一到两句话作答。",
        "简答题",
        "",
        "",
        "",
        "",
        "",
        "",
        "估计学习者知识状态",
        "",
        "用于估计学习者随时间变化的知识状态。",
        "",
        "KC1",
        "KC2；KC5",
        "KC2>KC3；KC5>KC6",
    ),
    _sample_row(
        "6",
        "圆周率保留两位小数约为多少？",
        "数值题",
        "",
        "",
        "",
        "",
        "",
        "",
        "3.14",
        "",
        "按四舍五入保留两位小数。\n答案须按数值填写。",
        "0.250",
        "KC1",
        "KC2",
        "KC2>KC3",
    ),
)


def build_question_import_template_csv() -> bytes:
    output = StringIO(newline="")
    writer = csv.DictWriter(
        output,
        fieldnames=QUESTION_IMPORT_TEMPLATE_HEADERS,
        lineterminator="\n",
    )
    writer.writeheader()
    writer.writerows(_image_example_rows(QUESTION_IMPORT_TEMPLATE_ROWS))
    return output.getvalue().encode("utf-8-sig")


def build_question_import_template_excel() -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "题目导入"
    sheet.append(QUESTION_IMPORT_TEMPLATE_HEADERS)
    for row in _image_example_rows(QUESTION_IMPORT_TEMPLATE_ROWS):
        sheet.append([row[header] for header in QUESTION_IMPORT_TEMPLATE_HEADERS])

    for cell in sheet[1]:
        cell.font = Font(bold=True)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    for row in sheet.iter_rows(min_row=2):
        for cell in row:
            cell.number_format = "@"
            cell.alignment = Alignment(wrap_text=True, vertical="top")
    for column in ("N", "O", "P"):
        sheet[f"{column}1"].comment = Comment(KC_TEMPLATE_INSTRUCTION, "KT Data Lab")
    sheet.freeze_panes = "A2"
    sheet['B1'].comment = Comment(IMAGE_TEMPLATE_INSTRUCTION, 'KT Data Lab')
    _embed_excel_images(sheet)

    output = BytesIO()
    workbook.save(output)
    workbook.close()
    return output.getvalue()


def _add_labeled_paragraph(document: Document, label: str, value: str):
    paragraph = document.add_paragraph()
    label_run = paragraph.add_run(f"【{label}】")
    label_run.bold = True
    previous = 0
    for match in _EXAMPLE_IMAGE_REFERENCE.finditer(value):
        paragraph.add_run(value[previous:match.start()])
        paragraph.add_run().add_picture(str(_IMAGE_ASSETS / match.group(1)),
                                       width=Inches(2.8 if label.startswith('选项') else 5.5))
        previous = match.end()
    paragraph.add_run(value[previous:])
    return paragraph


def build_question_import_template_word() -> bytes:
    document = Document()
    title_style = document.styles["Title"]
    title_style.font.color.rgb = RGBColor(0, 0, 0)
    title_properties = title_style.element.get_or_add_pPr()
    title_border = title_properties.find(qn("w:pBdr"))
    if title_border is not None:
        title_properties.remove(title_border)

    question_heading_style = document.styles["Heading 2"]
    question_heading_style.font.color.rgb = RGBColor(0, 0, 0)
    question_heading_style.paragraph_format.keep_with_next = True
    document.add_paragraph("题目与 KC 联合导入模板", style="Title")
    document.add_paragraph(f"填写说明：{KC_TEMPLATE_INSTRUCTION}")
    document.add_paragraph('填写说明：' + IMAGE_TEMPLATE_INSTRUCTION)

    for row in _image_example_rows(QUESTION_IMPORT_TEMPLATE_ROWS):
        question_paragraphs = [document.add_heading(f"第{row['序号']}题", level=2)]
        for header in QUESTION_IMPORT_TEMPLATE_HEADERS[1:]:
            value = row[header]
            if value:
                question_paragraphs.append(
                    _add_labeled_paragraph(
                        document,
                        "题干" if header == "题目" else header,
                        value,
                    )
                )
        for paragraph in question_paragraphs[:-1]:
            paragraph.paragraph_format.keep_with_next = True

    output = BytesIO()
    document.save(output)
    return output.getvalue()


def question_group_template_rows():
    import json
    from pathlib import Path
    from .unified_reader import GROUP_HEADERS
    examples = json.loads(Path(__file__).with_name('latest_template_data.json').read_text(encoding='utf-8'))
    return [{**dict.fromkeys(GROUP_HEADERS, ''), **{
        {'标题': '材料标题', '词库': '共享词库'}.get(key, key): str(value)
        for key, value in example.items()
    }} for example in examples]


def _multi_blank_example():
    headers = ('题型', '题目', '答案', '分值', '可接受答案', '解析', '一级知识点', '二级知识点', '三级知识点')
    rows = [
        ('填空题', '两个端口功率之和为零，U2=2U1、I2=kI1，则k=____；端口1功率为-6 W时，端口2功率为____ W。', '-1/2', '2', '', '1+2k=0。', 'KC1', '', ''),
        ('', '', '6', '1', '', '两个端口功率之和为零。', '', '', ''),
    ]
    instructions = ('填写说明：新题填写题型和题干；同题后续空紧接下一行，只填本空答案、分值、解析或知识点，题型和题干留空。'
                    '每个空用至少三个下划线表示，不合并单元格，不用填写题组编号。空白行会中断续行。'
                    '续行知识点全部留空时沿用首行；分值留空按每空1分。可接受答案仅用于本空的同义写法，以分号分隔。'
                    '纯数值答案按数值比较，支持分数与小数等价，其他答案按文本匹配；导入前核对预览。')
    return headers, rows, instructions


def build_question_group_template(extension):
    from .unified_reader import GROUP_HEADERS
    rows = _image_example_rows(question_group_template_rows())
    blank_headers, blank_rows, blank_instructions = _multi_blank_example()
    rows.extend({**dict.fromkeys(GROUP_HEADERS, ''), **dict(zip(blank_headers, row, strict=True))} for row in blank_rows)
    rows[0].update({'一级知识点': 'KC1', '二级知识点': 'KC2', '三级知识点': 'KC2>KC3'})
    if extension == 'csv':
        output = StringIO(newline='')
        writer = csv.DictWriter(output, fieldnames=GROUP_HEADERS)
        writer.writeheader()
        writer.writerows(rows)
        return output.getvalue().encode('utf-8-sig')
    output = BytesIO()
    if extension == 'xlsx':
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = '题目'
        sheet.append(GROUP_HEADERS)
        for row in rows:
            sheet.append([row[header] for header in GROUP_HEADERS])
        for row in sheet:
            for cell in row:
                cell.number_format = '@'
                cell.alignment = Alignment(wrap_text=True, vertical='top')
        sheet.freeze_panes = 'A2'
        sheet['B1'].comment = Comment(IMAGE_TEMPLATE_INSTRUCTION + '\n' + blank_instructions, 'KT Data Lab')
        _embed_excel_images(sheet)
        for header in ('一级知识点', '二级知识点', '三级知识点'):
            sheet.cell(1, GROUP_HEADERS.index(header) + 1).comment = Comment(KC_TEMPLATE_INSTRUCTION, 'KT Data Lab')
        notes = workbook.create_sheet('填写说明')
        notes.append([KC_TEMPLATE_INSTRUCTION])
        notes.append([IMAGE_TEMPLATE_INSTRUCTION])
        notes.append([blank_instructions])
        for line in ('11 类题型，13 道完整题，24 个作答项；包括多空填空和两种翻译方向。', '课程题库须填已有 KC，空 KC 仅保存为草稿。', '同一题组共享首行材料、标题、作答说明、词库、语言和大小写规则。', '词库用 | 分隔，文章空位 {{b01}} 与空位编号逐一对应。', '简答、翻译、作文沿用实验既有评阅规则；解析中的分项分值仅为教师参考。'):
            notes.append([line])
        notes.column_dimensions['A'].width = 90
        for row in notes:
            row[0].alignment = Alignment(wrap_text=True, vertical='top')
        workbook.save(output)
        workbook.close()
    elif extension == 'docx':
        document = Document()
        document.add_paragraph('完整题型导入模板', style='Title')
        document.add_paragraph('填写说明：' + KC_TEMPLATE_INSTRUCTION)
        document.add_paragraph('填写说明：' + IMAGE_TEMPLATE_INSTRUCTION)
        document.add_paragraph('填写说明：本模板含 11 类题型、13 道完整题目和 24 个作答项，包括两种翻译方向。请填写当前课程已有 KC；独立题库填写已有私有或公共知识点名称或 id:知识点ID。空 KC 仅保存为草稿。简答、翻译、作文采用实验既有二元人工判定规则，解析中的维度分值为参考说明。文件序号不作为服务器题号。')
        document.add_paragraph(blank_instructions)
        current_code = None
        for row in rows[:-len(blank_rows)]:
            code = row['题组编号'] or None
            if code != current_code:
                if current_code is not None:
                    _add_labeled_paragraph(document, '题组结束', '')
                current_code = code
                if code:
                    _add_labeled_paragraph(document, '题组开始', code)
                    for field in ('题组形式', '材料标题', '作答说明', '共享材料', '内容语言', '共享词库'):
                        if row[field]:
                            _add_labeled_paragraph(document, field, row[field])
            if code:
                _add_labeled_paragraph(document, '小题', row['组内顺序'])
            else:
                document.add_paragraph(f"第{row['序号']}题")
            for field in (*QUESTION_IMPORT_TEMPLATE_HEADERS[1:], '空位编号', '分值', '大小写敏感', '绝对误差容限', '相对误差容限', *(() if code else ('材料标题', '作答说明', '内容语言'))):
                if row[field]:
                    _add_labeled_paragraph(document, field, row[field])
        if current_code is not None:
            _add_labeled_paragraph(document, '题组结束', '')
        table = document.add_table(rows=1, cols=len(blank_headers))
        table.style = 'Table Grid'
        for cell, value in zip(table.rows[0].cells, blank_headers):
            cell.text = value
        for row in blank_rows:
            for cell, value in zip(table.add_row().cells, row):
                cell.text = value
        document.save(output)
    else:
        raise ValueError('不支持的题组模板格式。')
    return output.getvalue()
