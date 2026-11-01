"""Authoritative complete-question inventory (one row per ordinary item or group)."""
from django.urls import reverse

from apps.accounts.models import Course
from ..models import QuestionBank
from .question_groups import project_question_group
from .question_kc_mapping import project_question_kc_mapping
from .question_readiness import readiness_issues


def complete_questions(bank, *, query='', question_type='', status=''):
    writable = bank.visibility != QuestionBank.Visibility.ARCHIVED and (not bank.course_id or bank.course.status == Course.Status.ACTIVE)
    detail = reverse('courses:bank_detail', args=[bank.course_id, bank.pk]) if bank.course_id else reverse('question_bank:bank_detail', args=[bank.pk])
    rows = []
    questions = bank.questions.select_related('course_identity').prefetch_related('concept_links__concept', 'options').order_by('sequence', 'pk')
    for question in questions.filter(group__isnull=True):
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
    for group in bank.groups.prefetch_related('members__concept_links__concept', 'members__options'):
        document = project_question_group(group)
        members = list(group.members.all())
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
    query = query.casefold()
    matching_groups = set(bank.questions.filter(group__isnull=False, text__icontains=query).values_list('group_id', flat=True)) if query else set()
    return sorted([row for row in rows if (not question_type or row['question_type'] == question_type)
        and (not status or row['status'] == status)
        and (not query or (row['unit_kind'] == 'group' and row['id'] in matching_groups) or query in ' '.join(str(row.get(key, '')) for key in ('title', 'text', 'instructions', 'code', 'stable_code', 'kc_mapping', 'member_kc_mappings')).casefold())], key=lambda row: (row['sequence'], row['unit_key']))
