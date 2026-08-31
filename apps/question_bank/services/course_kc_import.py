from __future__ import annotations

from django.db import transaction

from apps.accounts.course_access import lock_writable_course

from ..models import Concept
from .course_kc import CourseKCService, normalize_course_kc_name
from .import_parsing import ParsedCourseKC


class CourseKCBulkImportError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def preview_course_kcs(*, course, parsed_rows: list[ParsedCourseKC]):
    existing = list(Concept.objects.filter(course=course))
    by_code = {concept.code: concept for concept in existing}
    by_name = {concept.normalized_name: concept for concept in existing}
    rows = []
    for row in parsed_rows:
        normalized_name = normalize_course_kc_name(row.name)
        code_match = by_code.get(row.code)
        name_match = by_name.get(normalized_name)
        item = {
            "sequence": row.sequence,
            "code": row.code,
            "name": row.name,
            "normalized_name": normalized_name,
        }
        if code_match is not None and code_match.normalized_name == normalized_name:
            item["classification"] = (
                "archived_unchanged"
                if code_match.status == Concept.Status.ARCHIVED
                else "unchanged"
            )
        elif code_match is not None:
            item.update(
                classification="conflict",
                error_code="kc_code_conflict",
                error=f"{row.code} 已绑定其他名称",
            )
        elif name_match is not None:
            item.update(
                classification="conflict",
                error_code="normalized_name_conflict",
                error=f"规范化名称已绑定 {name_match.code}",
            )
        else:
            item["classification"] = "new"
        rows.append(item)
    return {
        "schema_version": "course-kc-dictionary-v2",
        "rows": rows,
        "confirmable": all(row["classification"] != "conflict" for row in rows),
    }


@transaction.atomic
def apply_course_kc_import(*, course, payload):
    course = lock_writable_course(course)
    if not payload.get("confirmable"):
        raise CourseKCBulkImportError("preview_invalid", "预览包含冲突，不能确认导入")
    parsed_rows = [
        ParsedCourseKC(
            int(row["sequence"]),
            str(row["code"]),
            str(row["name"]),
        )
        for row in payload.get("rows", [])
    ]
    current = preview_course_kcs(course=course, parsed_rows=parsed_rows)
    expected_state = [
        (row.get("code"), row.get("normalized_name"), row.get("classification"))
        for row in payload.get("rows", [])
    ]
    current_state = [
        (row.get("code"), row.get("normalized_name"), row.get("classification"))
        for row in current["rows"]
    ]
    if not current["confirmable"] or current_state != expected_state:
        raise CourseKCBulkImportError(
            "preview_stale",
            "课程 KC 总库已变化，请重新生成预览",
        )
    created = 0
    unchanged = 0
    for row, current_row in zip(parsed_rows, current["rows"], strict=True):
        if current_row["classification"] == "new":
            CourseKCService.create(
                course=course,
                sequence=row.sequence,
                name=row.name,
            )
            created += 1
        else:
            unchanged += 1
    return {"created": created, "unchanged": unchanged}
