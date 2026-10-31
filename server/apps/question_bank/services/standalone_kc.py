"""Owner-scoped primary KC identities for standalone question groups."""
from django.core.exceptions import ValidationError
from django.db.models import Q

from ..models import Concept, Question, QuestionConcept


def available_standalone_concepts(bank):
    return Concept.objects.filter(course__isnull=True, status=Concept.Status.ACTIVE).filter(
        Q(scope=Concept.Scope.PUBLIC) | Q(scope=Concept.Scope.PRIVATE, owner_id=bank.owner_id)
    ).order_by('name', 'pk')


def standalone_primary_mapping(bank, concept_id):
    try:
        concept = available_standalone_concepts(bank).get(pk=concept_id)
    except (Concept.DoesNotExist, ValueError, TypeError):
        raise ValidationError('主知识点必须为题库所有者可用的私有或公共 KC。') from None
    return {'scope': 'standalone', 'primary_concept_id': concept.pk,
        'nodes': [{'concept_id': concept.pk, 'is_primary': True, 'order': 1, 'name': concept.name,
                   'kc_code': f'id:{concept.pk}', 'parent_kc_code': None}]}


def parse_standalone_primary(raw, bank):
    token = str(raw.get('一级知识点') or '').strip()
    if any(str(raw.get(key) or '').strip() for key in ('二级知识点', '三级知识点')):
        raise ValueError('独立题组使用一级主知识点；二三级课程 KC 映射请在课程题库中使用。')
    if not token:
        if str(raw.get('题组编号') or '').strip():
            raise ValueError('题组成员缺少主知识点。')
        return None
    concepts = available_standalone_concepts(bank)
    if token.startswith('id:'):
        return standalone_primary_mapping(bank, token[3:])
    matches = list(concepts.filter(Q(name=token) | Q(code=token)))
    if len(matches) != 1:
        raise ValueError(f'主知识点“{token}”不存在、不可用或名称不唯一；请填写已有名称或 id:知识点ID。')
    return standalone_primary_mapping(bank, matches[0].pk)


def project_standalone_primary(question):
    links = list(question.concept_links.select_related('concept').order_by('display_order', 'pk'))
    roots = [link for link in links if link.is_primary]
    if not links:
        return None
    return {'scope': 'standalone', 'primary_concept_id': roots[0].concept_id if len(roots) == 1 else None,
        'nodes': [{'concept_id': link.concept_id, 'is_primary': link.is_primary, 'order': link.display_order, 'name': link.concept.name,
                   'kc_code': f'id:{link.concept_id}', 'parent_kc_code': None} for link in links]}


def replace_standalone_primary(question, concept_id):
    mapping = standalone_primary_mapping(question.bank, concept_id) if concept_id is not None else None
    existing = list(question.concept_links.all())
    if mapping and any(link.is_primary and link.concept_id == concept_id for link in existing):
        return
    if question.status == Question.Status.ACTIVE:
        question.status = Question.Status.DRAFT
        question.save(update_fields=['status'])
    question.concept_links.all().delete()
    if mapping:
        QuestionConcept.objects.create(question=question, concept_id=mapping['primary_concept_id'], is_primary=True)
