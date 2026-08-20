from django.db import transaction
from ..access import lock_writable_bank_content
from .import_parsing import ParsedKCMapping, ParsedQuestion
from ..models import Concept, Question, QuestionConcept, QuestionOption

@transaction.atomic
def import_questions(bank, parsed_rows: list[ParsedQuestion]):
    bank = lock_writable_bank_content(bank)
    created = []
    for row in parsed_rows:
        question = Question.objects.create(
            bank=bank, sequence=row.sequence, question_type=row.question_type,
            text=row.text, correct_answer=row.answer, status=Question.Status.ACTIVE,
        )
        QuestionOption.objects.bulk_create([
            QuestionOption(question=question, key=key, text=value, display_order=index)
            for index, (key, value) in enumerate(row.options.items(), start=1)
        ])
        created.append(question)
    return created

def _get_or_create_concept(owner, name, course=None):
    normalized = " ".join(name.strip().casefold().split())
    if course is not None:
        concept, _ = Concept.objects.get_or_create(
            course=course,
            normalized_name=normalized,
            defaults={"name": name, "owner": None, "scope": Concept.Scope.PRIVATE},
        )
        return concept
    public = Concept.objects.filter(scope=Concept.Scope.PUBLIC, normalized_name=normalized).first()
    if public:
        return public
    if owner.is_superuser or owner.role == "admin":
        concept, _ = Concept.objects.get_or_create(
            scope=Concept.Scope.PUBLIC,
            normalized_name=normalized,
            defaults={"name": name, "owner": None},
        )
        return concept
    concept, _ = Concept.objects.get_or_create(
        owner=owner,
        scope=Concept.Scope.PRIVATE,
        normalized_name=normalized,
        defaults={"name": name},
    )
    return concept

@transaction.atomic
def import_kc_mappings(bank, owner, mappings: list[ParsedKCMapping]):
    bank = lock_writable_bank_content(bank)
    updated = 0
    for mapping in mappings:
        question = bank.questions.get(sequence=mapping.sequence)
        question.concept_links.all().delete()
        primary = _get_or_create_concept(owner, mapping.primary, bank.course)
        QuestionConcept.objects.create(question=question, concept=primary, is_primary=True)
        for name in mapping.secondary:
            QuestionConcept.objects.get_or_create(question=question, concept=_get_or_create_concept(owner, name, bank.course), defaults={"is_primary": False})
        updated += 1
    return updated


@transaction.atomic
def copy_shared_bank(source_bank, target_course, actor=None):
    from ..models import QuestionBank
    if actor is None:
        actor = target_course
        target_course = None
    if source_bank.visibility != QuestionBank.Visibility.SHARED:
        raise ValueError("只能复制已共享题库")
    target = QuestionBank.objects.create(
        course=target_course,
        created_by=actor,
        name=f"{source_bank.name}（副本）",
        description=source_bank.description,
        visibility=QuestionBank.Visibility.PRIVATE,
    )
    for source in source_bank.questions.prefetch_related("options", "concept_links__concept"):
        question = Question.objects.create(
            bank=target, sequence=source.sequence, code=source.code,
            question_type=source.question_type, text=source.text,
            correct_answer=source.correct_answer, accepted_answers=source.accepted_answers,
            absolute_tolerance=source.absolute_tolerance, relative_tolerance=source.relative_tolerance,
            explanation=source.explanation, teacher_difficulty=source.teacher_difficulty,
            empirical_difficulty=source.empirical_difficulty, status=source.status,
        )
        QuestionOption.objects.bulk_create([
            QuestionOption(question=question, key=o.key, text=o.text, display_order=o.display_order)
            for o in source.options.all()
        ])
        for link in source.concept_links.select_related("concept"):
            concept = _get_or_create_concept(actor, link.concept.name, target_course)
            QuestionConcept.objects.create(question=question, concept=concept, is_primary=link.is_primary)
    return target
