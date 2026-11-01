"""Authoritative complete-question inventory (one row per ordinary item or group)."""
from django.urls import reverse
from django.db.models import Min

from apps.accounts.models import Course
from ..models import QuestionBank
from .question_groups import project_question_group, project_group_status, question_groups_for_read
from .question_kc_mapping import project_question_kc_mapping, question_kc_mapping_prefetch
from .question_readiness import readiness_issues


def complete_questions(bank, *, query='', question_type='', status='', paginate=None):
    if query:
        # Keep the existing text/KC matching semantics; batch projections for searches.
        rows = complete_questions(bank, question_type=question_type, status=status)
        query = query.casefold()
        matching_groups = set(bank.questions.filter(group__isnull=False, text__icontains=query).values_list('group_id', flat=True))
        rows = [row for row in rows if (row['unit_kind'] == 'group' and row['id'] in matching_groups)
            or query in ' '.join(str(row.get(key, '')) for key in ('title', 'text', 'instructions', 'code', 'stable_code', 'kc_mapping', 'member_kc_mappings')).casefold()]
        return paginate(rows) if paginate else rows

    units = []
    for question in bank.questions.filter(group__isnull=True).values('pk', 'sequence', 'question_type', 'educational_type', 'status'):
        kind = question['question_type'] if question['educational_type'] == 'standard' else question['educational_type']
        if (not question_type or kind == question_type) and (not status or question['status'] == status):
            units.append((question['sequence'], f"question:{question['pk']}", question['pk'], 'question'))
    groups = bank.groups.all()
    if question_type:
        groups = groups.filter(kind=question_type)
    if status:
        group_ids = [group.pk for group in question_groups_for_read(groups) if project_group_status(group)['status'] == status]
        groups = groups.filter(pk__in=group_ids)
    for group in groups.annotate(first_sequence=Min('members__sequence')).values('pk', 'first_sequence'):
        units.append((group['first_sequence'] or 0, f"group:{group['pk']}", group['pk'], 'group'))
    units.sort(key=lambda unit: unit[:2])
    if paginate:
        units = paginate(units)
    return _project_complete_questions(bank,
        question_ids=[unit[2] for unit in units if unit[3] == 'question'],
        group_ids=[unit[2] for unit in units if unit[3] == 'group'])


def _project_complete_questions(bank, *, question_ids, group_ids):
    writable = bank.visibility != QuestionBank.Visibility.ARCHIVED and (not bank.course_id or bank.course.status == Course.Status.ACTIVE)
    detail = reverse('courses:bank_detail', args=[bank.course_id, bank.pk]) if bank.course_id else reverse('question_bank:bank_detail', args=[bank.pk])
    rows = []
    questions = bank.questions.filter(pk__in=question_ids).select_related('course_identity').prefetch_related(
        question_kc_mapping_prefetch(include_children=True), 'options').order_by('sequence', 'pk')
    for question in questions.filter(group__isnull=True):
        question.bank = bank
        identity = getattr(question, 'course_identity', None)
        item_type = question.educational_type if question.educational_type != 'standard' else question.question_type
        rows.append({
            'unit_key': f'question:{question.pk}', 'unit_kind': 'question', 'id': question.pk,
            'sequence': question.sequence, 'code': question.code,
            'course_sequence': identity.course_sequence if identity else None, 'stable_code': identity.stable_code if identity else '',
            'title': question.title, 'text': question.text, 'instructions': question.instructions,
            'question_type': item_type, 'question_type_label': question.get_educational_type_display() if question.educational_type != 'standard' else question.get_question_type_display(),
            'educational_type': question.educational_type, 'group_id': None, 'group_order': None, 'blank_id': '',
            'max_score': format(question.max_score, '.2f'), 'content_language': question.content_language,
            'text_match_case_sensitive': question.text_match_case_sensitive, 'text_match_policy_version': question.text_match_policy_version,
            'teacher_difficulty': str(question.teacher_difficulty) if question.teacher_difficulty is not None else None,
            'status': question.status, 'status_label': question.get_status_display(),
            'draft_reasons': readiness_issues(question) if question.status == 'draft' else [],
            'kc_mapping': project_question_kc_mapping(question), 'member_kc_mappings': [],
            'edit_url': reverse('courses:question_edit', args=[bank.course_id, bank.pk, question.pk]) if writable and bank.course_id else reverse('question_bank:question_edit', args=[bank.pk, question.pk]) if writable else None,
            'delete_url': reverse('teacher-question-delete', args=[bank.course_id, bank.pk, question.pk]) if writable and bank.course_id else None,
        })
    for group in question_groups_for_read(bank.groups.filter(pk__in=group_ids)):
        document = project_question_group(group)
        members = sorted(group._read_members, key=lambda member: member.sequence)
        rows.append({
            'unit_key': f'group:{group.pk}', 'unit_kind': 'group', 'id': group.pk,
            'sequence': min((member.sequence for member in members), default=0), 'code': '', 'course_sequence': None, 'stable_code': '',
            'title': group.title, 'text': group.material, 'instructions': group.instructions,
            'question_type': group.kind, 'question_type_label': group.get_kind_display(), 'educational_type': 'standard',
            'group_id': group.pk, 'group_order': None, 'blank_id': '', 'max_score': document['total_max_score'],
            'content_language': group.content_language, 'text_match_case_sensitive': False, 'text_match_policy_version': 'text-exact-v1',
            'teacher_difficulty': None, 'status': document['status'], 'status_label': '可用' if document['status'] == 'active' else '草稿',
            'draft_reasons': document['readiness_issues'],
            'kc_mapping': {'schema_version': 'question-kc-tree-v2', 'root_kc_code': None, 'nodes': [], 'mapping_hash': ''},
            'member_kc_mappings': [{'id': member.pk, 'order': member.group_order, 'mapping': project_question_kc_mapping(member)} for member in members],
            'edit_url': f'{detail}questions/0/edit/?group={group.pk}' if writable else None,
            'delete_url': f'/api/v1/teacher/courses/{bank.course_id}/banks/{bank.pk}/groups/{group.pk}/' if writable and bank.course_id else None,
        })
    return sorted(rows, key=lambda row: (row['sequence'], row['unit_key']))
