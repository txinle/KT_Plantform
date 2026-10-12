from __future__ import annotations

from collections import defaultdict

from django.db import transaction

from ..access import lock_writable_bank_content
from ..models import Concept, Question
from .course_kc import CourseKCResolutionError, resolve_course_kc_reference
from .import_parsing import ParsedQuestionKCTree
from .question_kc_mapping import replace_question_kc_mapping


class QuestionKCTreeImportError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def _resolve(course, token):
    try:
        resolution = resolve_course_kc_reference(course=course, token=token)
    except CourseKCResolutionError as error:
        raise QuestionKCTreeImportError(error.code, str(error)) from error
    return resolution, {
        "raw_token": resolution.raw_token,
        "normalized_token": resolution.normalized_token,
        "method": resolution.method,
        "kc_code": resolution.concept.code,
        "final_name": resolution.concept.name,
        "distance": resolution.distance,
    }


def preview_question_kc_trees(*, bank, parsed_rows: list[ParsedQuestionKCTree]):
    questions = {
        question.sequence: question
        for question in bank.questions.filter(
            sequence__in=[row.sequence for row in parsed_rows]
        )
    }
    trees = []
    for row in parsed_rows:
        question = questions.get(row.sequence)
        if question is None:
            raise QuestionKCTreeImportError(
                "question_not_in_bank",
                f"题目序号 {row.sequence} 不属于当前题库",
            )

        trace = []
        root_resolution, root_trace = _resolve(bank.course, row.root)
        trace.append(root_trace)
        level_two = []
        for token in row.level_two:
            resolution, item_trace = _resolve(bank.course, token)
            level_two.append(resolution.concept.code)
            trace.append(item_trace)
        level_two_set = set(level_two)

        resolved_third = []
        for raw_parent, raw_child in row.level_three:
            parent_resolution, parent_trace = _resolve(bank.course, raw_parent)
            child_resolution, child_trace = _resolve(bank.course, raw_child)
            trace.extend([parent_trace, child_trace])
            parent_code = parent_resolution.concept.code
            if parent_code not in level_two_set:
                raise QuestionKCTreeImportError(
                    "third_parent_not_level_two",
                    f"题目序号 {row.sequence} 的三级父 KC {parent_code} 不在二级集合中",
                )
            resolved_third.append((parent_code, child_resolution.concept.code))

        root_code = root_resolution.concept.code
        node_codes = [root_code, *level_two, *(child for _parent, child in resolved_third)]
        if len(node_codes) != len(set(node_codes)):
            raise QuestionKCTreeImportError(
                "kc_repeated",
                f"题目序号 {row.sequence} 的同一 KC 不能重复或拥有多个父节点",
            )

        nodes = [{"kc_code": root_code, "parent_kc_code": None, "order": 1}]
        nodes.extend(
            {
                "kc_code": code,
                "parent_kc_code": root_code,
                "order": order,
            }
            for order, code in enumerate(level_two, start=1)
        )
        child_orders = defaultdict(int)
        for parent_code, child_code in resolved_third:
            child_orders[parent_code] += 1
            nodes.append(
                {
                    "kc_code": child_code,
                    "parent_kc_code": parent_code,
                    "order": child_orders[parent_code],
                }
            )
        trees.append(
            {
                "sequence": row.sequence,
                "question_id": question.pk,
                "root_kc_code": root_code,
                "nodes": nodes,
                "match_trace": trace,
            }
        )
    return {"schema_version": "question-kc-tree-v2", "trees": trees}


@transaction.atomic
def apply_question_kc_tree_import(*, bank, payload):
    bank = lock_writable_bank_content(bank)
    trees = payload.get("trees") or []
    questions = {
        question.sequence: question
        for question in Question.objects.select_for_update().filter(
            bank=bank,
            sequence__in=[tree.get("sequence") for tree in trees],
        )
    }
    current_concepts = {
        concept.code: concept
        for concept in Concept.objects.select_for_update().filter(course=bank.course)
    }
    for tree in trees:
        sequence = tree.get("sequence")
        question = questions.get(sequence)
        if question is None or question.pk != tree.get("question_id"):
            raise QuestionKCTreeImportError(
                "preview_stale",
                f"题目序号 {sequence} 已变化，请重新预览",
            )
        for trace in tree.get("match_trace", []):
            concept = current_concepts.get(trace.get("kc_code"))
            if (
                concept is None
                or concept.status != Concept.Status.ACTIVE
                or concept.name != trace.get("final_name")
            ):
                raise QuestionKCTreeImportError(
                    "preview_stale",
                    f"{trace.get('kc_code')} 已变化，请重新预览",
                )
        replace_question_kc_mapping(
            question,
            root_kc_code=tree["root_kc_code"],
            nodes=tree["nodes"],
        )
    return {"questions_updated": len(trees)}
