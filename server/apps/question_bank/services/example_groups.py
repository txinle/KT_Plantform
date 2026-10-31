import csv
from io import StringIO

from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction

from ..models import Concept, QuestionBank
from .import_templates import question_group_template_rows
from .unified_import import UnifiedQuestionImportService
from .unified_reader import GROUP_HEADERS


@transaction.atomic
def ensure_english_group_examples(teacher):
    """Seed the approved twelve complete examples through the real import contract."""
    bank, _ = QuestionBank.objects.get_or_create(owner=teacher, course=None,
        name='完整题型示例：11类题型与12道完整题', defaults={'created_by': teacher, 'description': '最新题库示例，含阅读、选词、短文填空、双向翻译与作文；不改写已有演示实验。'})
    if bank.questions.exists() or bank.groups.exists():
        return bank
    concepts = {}
    for key, name in (('language', 'English reading and vocabulary'), ('writing', 'English written expression'), ('eda', 'Digital logic and EDA')):
        concepts[key], _ = Concept.objects.get_or_create(owner=teacher, scope=Concept.Scope.PRIVATE, normalized_name=name.casefold(), defaults={'name': name})
    rows = []
    for row in question_group_template_rows():
        domain = 'language' if row['题组编号'] else 'writing' if row['题型'] in ('英汉互译', '作文题') else 'eda'
        rows.append({**row, '一级知识点': f"id:{concepts[domain].pk}"})
    output = StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=GROUP_HEADERS)
    writer.writeheader()
    writer.writerows(rows)
    args = {'bank': bank, 'actor': teacher, 'session_key': 'seed-demo-latest-question-types'}
    preview = UnifiedQuestionImportService.preview(**args, uploaded=SimpleUploadedFile('latest-demo.csv', output.getvalue().encode('utf-8-sig')))
    UnifiedQuestionImportService.confirm(**args, preview_id=preview['preview_id'])
    return bank
