import hashlib
import json

from django.core.exceptions import ValidationError
from django.db import transaction

from ..models import Concept, Question, QuestionConcept


SCHEMA_VERSION = "question-kc-tree-v2"


def _validated_tree(question, *, root_kc_code, nodes):
    rows = [
        {
            "kc_code": str(row.get("kc_code") or "").strip(),
            "parent_kc_code": (
                str(row.get("parent_kc_code")).strip()
                if row.get("parent_kc_code") not in (None, "")
                else None
            ),
            "order": int(row.get("order") or 1),
        }
        for row in nodes
    ]
    codes = [row["kc_code"] for row in rows]
    if not rows or not root_kc_code:
        raise ValidationError("题目 KC 映射必须包含一个主 KC。")
    if len(codes) != len(set(codes)):
        raise ValidationError("同一 KC 在一道题的映射中不能重复或拥有多个父节点。")
    if any(not code for code in codes):
        raise ValidationError("KC 代码不能为空。")
    by_code = {row["kc_code"]: row for row in rows}
    root_kc_code = str(root_kc_code).strip()
    roots = [row for row in rows if row["parent_kc_code"] is None]
    if len(roots) != 1 or roots[0]["kc_code"] != root_kc_code:
        raise ValidationError("题目 KC 映射必须且只能有一个主 KC 根节点。")
    if any(row["order"] <= 0 for row in rows):
        raise ValidationError("KC 同级顺序必须为正整数。")
    if any(
        row["parent_kc_code"] is not None
        and row["parent_kc_code"] not in by_code
        for row in rows
    ):
        raise ValidationError("题目 KC 映射包含孤立节点或不存在的父 KC。")

    depths = {}

    def depth_for(code, trail=()):
        if code in depths:
            return depths[code]
        if code in trail:
            raise ValidationError("题目 KC 映射不能形成循环。")
        parent_code = by_code[code]["parent_kc_code"]
        depth = 1 if parent_code is None else depth_for(parent_code, (*trail, code)) + 1
        if depth > 3:
            raise ValidationError("题目 KC 映射最多三级。")
        depths[code] = depth
        return depth

    for code in codes:
        depth_for(code)

    concepts = {
        concept.code: concept
        for concept in Concept.objects.filter(
            course_id=question.bank.course_id,
            code__in=codes,
        )
    }
    if set(concepts) != set(codes):
        raise ValidationError("题目 KC 映射包含不属于当前课程的 kc_code。")
    archived = sorted(
        code for code, concept in concepts.items()
        if concept.status == Concept.Status.ARCHIVED
    )
    if archived:
        raise ValidationError(f"已归档 KC 不能加入新的题目映射：{', '.join(archived)}")
    return rows, concepts, depths


def validate_question_kc_mapping(question, *, root_kc_code, nodes):
    _validated_tree(question, root_kc_code=root_kc_code, nodes=nodes)


@transaction.atomic
def replace_question_kc_mapping(question, *, root_kc_code, nodes):
    requested_status = (
        Question.Status.ACTIVE
        if getattr(question, "_activate_after_primary_mapping", False)
        else question.status
    )
    question = Question.objects.select_for_update().select_related("bank").get(pk=question.pk)
    rows, concepts, depths = _validated_tree(
        question,
        root_kc_code=root_kc_code,
        nodes=nodes,
    )
    desired_status = requested_status
    if desired_status == Question.Status.ACTIVE:
        question.status = Question.Status.DRAFT
        question.save(update_fields=["status"])
    question.concept_links.all().delete()
    created = {}
    for row in sorted(rows, key=lambda item: (depths[item["kc_code"]], item["order"], item["kc_code"])):
        code = row["kc_code"]
        parent_code = row["parent_kc_code"]
        created[code] = QuestionConcept.objects.create(
            question=question,
            concept=concepts[code],
            is_primary=parent_code is None,
            parent_link=created.get(parent_code),
            display_order=row["order"],
        )
    if desired_status == Question.Status.ACTIVE:
        question.status = Question.Status.ACTIVE
        question.save(update_fields=["status"])
    return project_question_kc_mapping(question)


def project_question_kc_mapping(question):
    links = list(
        question.concept_links.select_related("concept", "parent_link__concept").order_by(
            "display_order", "concept__code", "pk"
        )
    )
    if not links:
        return {
            "schema_version": SCHEMA_VERSION,
            "root_kc_code": None,
            "nodes": [],
            "mapping_hash": hashlib.sha256(b"[]").hexdigest(),
        }
    by_id = {link.pk: link for link in links}
    depths = {}

    def depth_for(link):
        if link.pk not in depths:
            depths[link.pk] = 1 if link.parent_link_id is None else depth_for(by_id[link.parent_link_id]) + 1
        return depths[link.pk]

    nodes = [
        {
            "kc_code": link.concept.code,
            "name": link.concept.name,
            "description": link.concept.description,
            "status": link.concept.status,
            "depth": depth_for(link),
            "parent_kc_code": link.parent_link.concept.code if link.parent_link_id else None,
            "order": link.display_order,
        }
        for link in links
    ]
    nodes.sort(key=lambda node: (node["depth"], node["order"], node["kc_code"]))
    canonical = json.dumps(nodes, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    root = next((node for node in nodes if node["parent_kc_code"] is None), None)
    return {
        "schema_version": SCHEMA_VERSION,
        "root_kc_code": root["kc_code"] if root else None,
        "nodes": nodes,
        "mapping_hash": hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
    }
