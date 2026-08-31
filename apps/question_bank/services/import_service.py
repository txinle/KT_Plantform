from decimal import Decimal

from django.db import transaction
from apps.accounts.course_access import lock_writable_course

from ..access import lock_question_bank, lock_writable_bank_content
from .import_parsing import ParsedKCMapping, ParsedKCWorkbook, ParsedQuestion
from .course_kc import normalize_course_kc_name, resolve_course_kc_reference
from .question_kc_mapping import project_question_kc_mapping, replace_question_kc_mapping
from ..models import Concept, Question, QuestionConcept, QuestionOption

@transaction.atomic
def import_questions(bank, parsed_rows: list[ParsedQuestion]):
    bank = lock_writable_bank_content(bank)
    sequences = [row.sequence for row in parsed_rows]
    duplicate_sequences = sorted(
        sequence for sequence in set(sequences) if sequences.count(sequence) > 1
    )
    if duplicate_sequences:
        raise ValueError(f"导入批次包含重复序号：{duplicate_sequences}")
    existing_sequences = list(
        bank.questions.filter(sequence__in=sequences)
        .order_by("sequence")
        .values_list("sequence", flat=True)
    )
    if existing_sequences:
        raise ValueError(f"题库中已存在以下序号：{existing_sequences}")
    created = []
    for row in parsed_rows:
        question = Question.objects.create(
            bank=bank, sequence=row.sequence, question_type=row.question_type,
            text=row.text, correct_answer=row.answer, accepted_answers=row.accepted_answers,
            explanation=row.explanation,
            teacher_difficulty=(
                Decimal(row.teacher_difficulty)
                if row.teacher_difficulty is not None
                else None
            ),
            status=Question.Status.DRAFT,
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
        concept = Concept.objects.filter(
            course=course,
            normalized_name=normalized,
            status=Concept.Status.ACTIVE,
        ).first()
        if concept is None:
            raise ValueError(f"课程 KC 总库中不存在可用知识点：{name}")
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
        if bank.course_id is None:
            raise ValueError("旧版 KC 映射仅支持已归属课程的题库")
        primary = resolve_course_kc_reference(course=bank.course, token=mapping.primary).concept
        secondary = [
            resolve_course_kc_reference(course=bank.course, token=token).concept
            for token in mapping.secondary
        ]
        if primary in secondary:
            raise ValueError("主 KC 不能同时作为次 KC")
        question._activate_after_primary_mapping = True
        replace_question_kc_mapping(
            question,
            root_kc_code=primary.code,
            nodes=[
                {"kc_code": primary.code, "parent_kc_code": None, "order": 1},
                *[
                    {
                        "kc_code": concept.code,
                        "parent_kc_code": primary.code,
                        "order": order,
                    }
                    for order, concept in enumerate(secondary, start=1)
                ],
            ],
        )
        updated += 1
    return updated


@transaction.atomic
def import_kc_workbook(bank, workbook: ParsedKCWorkbook):
    bank = lock_writable_bank_content(bank)
    course = lock_writable_course(bank.course)
    existing_by_code = {
        concept.code: concept
        for concept in Concept.objects.filter(course=course)
    }
    for row in workbook.nodes:
        concept = existing_by_code.get(row.code)
        if concept is None:
            raise ValueError(f"课程 KC 总库中不存在 {row.code}")
        if concept.status != Concept.Status.ACTIVE:
            raise ValueError(f"{row.code} 已归档，不能加入新的题目映射")
        if concept.normalized_name != normalize_course_kc_name(row.name):
            raise ValueError(f"{row.code} 的名称与课程 KC 总库不一致")

    relations_by_question = {}
    for relation in workbook.relations:
        relations_by_question.setdefault(relation.stable_question_code, []).append(relation)

    for root in workbook.roots:
        question = bank.questions.select_related("course_identity").get(
            course_identity__stable_code=root.stable_question_code
        )
        nodes = [{"kc_code": root.primary_kc_code, "parent_kc_code": None, "order": 1}]
        nodes.extend(
            {
                "kc_code": relation.child_kc_code,
                "parent_kc_code": relation.parent_kc_code,
                "order": relation.child_order,
            }
            for relation in relations_by_question.get(root.stable_question_code, [])
        )
        replace_question_kc_mapping(
            question,
            root_kc_code=root.primary_kc_code,
            nodes=nodes,
        )
    return {"nodes_validated": len(workbook.nodes), "questions_updated": len(workbook.roots)}


@transaction.atomic
def copy_shared_bank(source_bank, target_course, actor=None):
    from ..models import QuestionBank
    if actor is None:
        actor = target_course
        target_course = None
    if target_course is not None:
        target_course = lock_writable_course(target_course)
    source_bank = lock_question_bank(source_bank)
    if source_bank.visibility != QuestionBank.Visibility.SHARED:
        raise ValueError("只能复制已共享题库")
    target = QuestionBank.objects.create(
        course=target_course,
        created_by=actor,
        name=source_bank.name,
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
        if target_course is not None and source.concept_links.exists():
            projection = project_question_kc_mapping(source)
            target_by_source_code = {}
            for node in projection["nodes"]:
                normalized = " ".join(node["name"].strip().casefold().split())
                concept, _created = Concept.objects.get_or_create(
                    course=target_course,
                    normalized_name=normalized,
                    defaults={
                        "name": node["name"],
                        "description": node.get("description", ""),
                    },
                )
                target_by_source_code[node["kc_code"]] = concept
            replace_question_kc_mapping(
                question,
                root_kc_code=target_by_source_code[projection["root_kc_code"]].code,
                nodes=[
                    {
                        "kc_code": target_by_source_code[node["kc_code"]].code,
                        "parent_kc_code": (
                            target_by_source_code[node["parent_kc_code"]].code
                            if node["parent_kc_code"]
                            else None
                        ),
                        "order": node["order"],
                    }
                    for node in projection["nodes"]
                ],
            )
        elif target_course is None:
            for link in source.concept_links.select_related("concept"):
                concept = _get_or_create_concept(actor, link.concept.name, None)
                QuestionConcept.objects.create(
                    question=question, concept=concept, is_primary=link.is_primary
                )
    return target
