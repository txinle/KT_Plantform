"""Stage document images in import rows without writing media files."""
import re
from pathlib import Path

from docx.oxml import OxmlElement
from docx.oxml.ns import qn

from .question_images import canonical_image


IMAGE_CONTENT_FIELDS = frozenset(('题目', '题干', '解析', '共享材料', *[f'选项{key}' for key in 'ABCDEF']))
_REFERENCE = re.compile(r'\{\{image:([^{}\r\n]+)\}\}')
_DRAWING_ERROR = 'kt_image_error'


def stage_image_references(rows, *, assets=None, image_files=()):
    """Resolve filenames and retain only the assets used by each content row."""
    available = dict(assets or {})
    names, invalid = {}, {}
    for uploaded in image_files:
        filename = Path(uploaded.name.replace('\\', '/')).name
        try:
            key, data_uri = canonical_image(uploaded.read(), filename)
        except ValueError as error:
            invalid[filename] = f'图片 {filename} 无效：{error}'
            continue
        if filename in names and names[filename] != key:
            invalid[filename] = f'图片文件名重复且内容不同：{filename}'
        names[filename] = key
        available[key] = data_uri

    for row in rows:
        used = {}
        for field, value in row.items():
            if (not field.startswith('_') and field not in IMAGE_CONTENT_FIELDS
                    and isinstance(value, str) and _REFERENCE.search(value)):
                row['_errors'].append(f'{field}不支持图片；图片仅可用于题干、选项、共享材料或解析。')

        def replace(match):
            filename = match.group(1)
            if filename in invalid:
                message = invalid[filename]
            else:
                key = names.get(filename, filename)
                if key in available:
                    used[key] = available[key]
                    return '{{image:' + key + '}}'
                message = f'找不到图片附件：{filename}'
            if message not in row['_errors']:
                row['_errors'].append(message)
            return match.group(0)

        for field in IMAGE_CONTENT_FIELDS:
            value = row.get(field)
            if isinstance(value, str):
                row[field] = _REFERENCE.sub(replace, value)
        if used:
            row['_image_assets'] = used
    return rows


def stage_word_images(document):
    """Replace supported inline drawings with text at their original run position."""
    assets = {}
    for drawing in list(document.element.body.iter(qn('w:drawing'))):
        blips = list(drawing.iter(qn('a:blip')))
        if any(child.tag == qn('wp:anchor') for child in drawing):
            error = '不支持浮动图片，请将图片布局改为嵌入型。'
        elif any(blip.get(qn('r:link')) for blip in blips):
            error = '不支持外部链接图片，请嵌入本地图片。'
        elif (len(drawing) != 1 or drawing[0].tag != qn('wp:inline') or len(blips) != 1
              or len(list(drawing.iter(qn('pic:pic')))) != 1):
            continue
        else:
            try:
                relationship = document.part.rels[blips[0].get(qn('r:embed'))]
                if relationship.is_external:
                    raise ValueError('不支持外部链接图片，请嵌入本地图片。')
                part = relationship.target_part
                key, data_uri = canonical_image(part.blob, str(part.partname))
            except (KeyError, ValueError) as exception:
                error = f'无法读取嵌入图片：{exception}'
            else:
                assets[key] = data_uri
                text = OxmlElement('w:t')
                text.text = '{{image:' + key + '}}'
                drawing.getparent().replace(drawing, text)
                continue
        drawing.set(_DRAWING_ERROR, error)
    return assets


def word_drawing_error(element):
    return element.get(_DRAWING_ERROR, '包含不支持的图片对象')


def stage_excel_images(sheet):
    """Use the top-left anchor cell as the explicit content association."""
    assets, errors = {}, {}
    for image in sheet._images:
        anchor = image.anchor
        origin = getattr(anchor, '_from', None)
        if origin is None:
            raise ValueError('Excel 图片必须锚定到题目、选项、共享材料或解析的单元格。')
        number, column = origin.row + 1, origin.col + 1
        cell = sheet.cell(number, column)
        field = str(sheet.cell(1, column).value or '').strip()
        if number == 1:
            raise ValueError('Excel 图片不能放在表头，请锚定到内容单元格。')
        if field not in IMAGE_CONTENT_FIELDS:
            errors.setdefault(number, []).append('图片须锚定到题目、选项、共享材料或解析单元格。')
            continue
        try:
            key, data_uri = canonical_image(image._data(), f'embedded.{image.format}')
        except ValueError as error:
            errors.setdefault(number, []).append(f'无法读取嵌入图片：{error}')
            continue
        assets[key] = data_uri
        token = '{{image:' + key + '}}'
        cell.value = f'{cell.value}\n{token}' if cell.value is not None and str(cell.value) else token
    return assets, errors
