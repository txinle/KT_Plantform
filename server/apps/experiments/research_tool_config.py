from django.core.exceptions import ValidationError


def validate_research_tools(value):
    if not isinstance(value, dict) or set(value) - {'doubt_enabled', 'check_enabled', 'kc_visible', 'hints', 'rules'}:
        raise ValidationError('研究工具配置包含不支持的字段。')
    for flag in ('doubt_enabled', 'check_enabled', 'kc_visible'):
        if flag in value and not isinstance(value[flag], bool):
            raise ValidationError('工具开关必须为布尔值。')
    hints = value.get('hints', [])
    rules = value.get('rules', [])
    if not isinstance(hints, list) or not isinstance(rules, list):
        raise ValidationError('提示和规则必须为列表。')
    hint_ids, rule_ids = set(), set()
    for hint in hints:
        required = {'id', 'question_code', 'level', 'text', 'contains_answer'}
        if not isinstance(hint, dict) or set(hint) != required:
            raise ValidationError('每个提示必须指定题目、级别、正文和是否包含答案。')
        if (not isinstance(hint['id'], str) or not hint['id'] or hint['id'] in hint_ids
                or not isinstance(hint['question_code'], str) or not hint['question_code']
                or not isinstance(hint['text'], str) or not hint['text'].strip()
                or type(hint['level']) is not int or hint['level'] < 1
                or type(hint['contains_answer']) is not bool):
            raise ValidationError('提示标识、题目、级别或正文无效。')
        hint_ids.add(hint['id'])
    for rule in rules:
        required = {'id', 'tool', 'trigger', 'question_code', 'after_seconds', 'max_exposures', 'required', 'hint_id'}
        if not isinstance(rule, dict) or set(rule) != required:
            raise ValidationError('规则字段不完整。')
        if (not isinstance(rule['id'], str) or not rule['id'] or rule['id'] in rule_ids
                or rule['tool'] not in {'confidence', 'check', 'hint'}
                or rule['trigger'] not in {'item_enter', 'elapsed_seconds'}
                or not isinstance(rule['question_code'], str)
                or type(rule['after_seconds']) is not int or rule['after_seconds'] < 0
                or type(rule['max_exposures']) is not int or rule['max_exposures'] < 1
                or type(rule['required']) is not bool
                or (rule['tool'] == 'hint' and rule['hint_id'] not in hint_ids)):
            raise ValidationError('仅支持有限的信心、检查和固定提示规则。')
        rule_ids.add(rule['id'])
        if rule['tool'] == 'check' and not value.get('check_enabled', False):
            raise ValidationError('检查提醒需要先启用检查工具。')
        if rule['tool'] == 'hint':
            hint = next(row for row in hints if row['id'] == rule['hint_id'])
            if hint['contains_answer']:
                raise ValidationError('作答阶段的自动提示不能包含正确答案或完整解答。')
            if rule['question_code'] and rule['question_code'] != hint['question_code']:
                raise ValidationError('提示规则与提示所属题目不一致。')
    return value
