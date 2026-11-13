"""Turn consecutive answer rows into one server-owned fill-blank group."""
import re

from .numeric import normalize_numeric_answer


PLAIN_BLANK = re.compile(r'_{3,}|＿{3,}')


def blank_material(text, count):
    """Number explicit blanks; image-only questions use a separate answer area."""
    matches = list(PLAIN_BLANK.finditer(text))
    if not matches and '{{image:' in text:
        return text + '\n' + ' '.join('{{b%02d}}' % number for number in range(1, count + 1))
    if len(matches) != count:
        raise ValueError(f'题干有 {len(matches)} 个空位，但有 {count} 行答案；请核对下划线与答案行。')
    index = iter(range(1, count + 1))
    return PLAIN_BLANK.sub(lambda _match: '{{b%02d}}' % next(index), text)


def expand_blank_rows(source):
    rows = [dict(row) for row in source]
    head = None
    groups = []
    current = []
    for row in rows:
        text = str(row.get('题目') or '').strip()
        kind = str(row.get('题型') or '').strip()
        if row.get('_break_before') or row.get('题组编号'):
            head = None
            current = []
        if row.get('题组编号'):
            continue
        if text or kind:
            head = row if text and kind in ('填空题', '填空', 'fill_blank') else None
            current = [row] if head else []
            if head:
                groups.append(current)
            continue
        has_answer = row.get('答案') is not None and bool(str(row['答案']).strip())
        unexpected = any(str(row.get(key) or '').strip() for key in
                         ('材料标题', '作答说明', '题组形式', '共享材料', '组内顺序', '空位编号', '共享词库', *[f'选项{k}' for k in 'ABCDEF']))
        unexpected = unexpected or bool(row.get('序号') and not row.get('_generated_sequence'))
        if not head or not has_answer or unexpected:
            row['_errors'] = [*row.get('_errors', []), '答案续行必须紧接填空题，题型和题干留空，仅填写本空答案、分值、解析或知识点。']
            head = None
            current = []
        else:
            current.append(row)
    used_codes = {str(row.get('题组编号')) for row in rows if row.get('题组编号')}
    for index, members in enumerate(groups, 1):
        first = members[0]
        count = len(PLAIN_BLANK.findall(str(first.get('题目', ''))))
        if len(members) == 1 and count <= 1:
            continue
        try:
            material = blank_material(str(first['题目']), len(members))
        except ValueError as error:
            for member in members:
                member['_errors'] = [*member.get('_errors', []), str(error)]
            continue
        code = f'填空题{index}'
        while code in used_codes:
            code += '-续'
        used_codes.add(code)
        for order, row in enumerate(members, 1):
            row.update({'题组编号': code, '题组形式': 'fill_blank',
                        '共享材料': material, '内容语言': first.get('内容语言') or 'und', '组内顺序': str(order), '空位编号': f'b{order:02}',
                        '题型': '填空题', '题目': '', '_legacy_standalone': False})
            if not any(str(row.get(column) or '').strip() for column in ('一级知识点', '二级知识点', '三级知识点')):
                for column in ('一级知识点', '二级知识点', '三级知识点'):
                    row[column] = first.get(column, '')
            try:
                normalize_numeric_answer(row.get('答案'))
            except ValueError:
                pass
            else:
                if not str(row.get('可接受答案') or '').strip():
                    row['题型'] = '数值题'
    return rows
