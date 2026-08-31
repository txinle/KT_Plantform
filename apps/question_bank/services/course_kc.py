from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction


KC_CODE_PATTERN = re.compile(r"^KC[1-9][0-9]*$")


@dataclass(frozen=True)
class CourseKCResolution:
    concept: object
    raw_token: str
    normalized_token: str
    method: str
    distance: int = 0


class CourseKCResolutionError(ValueError):
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(message)


def normalize_course_kc_name(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "")).casefold()
    without_punctuation = "".join(
        character
        for character in normalized
        if not unicodedata.category(character).startswith("P")
    )
    return " ".join(without_punctuation.split())


def course_kc_code(sequence: int) -> str:
    try:
        number = int(sequence)
    except (TypeError, ValueError):
        raise ValidationError("KC 序号必须为正整数。") from None
    if number <= 0 or str(number) != str(sequence).strip():
        raise ValidationError("KC 序号必须为正整数。")
    return f"KC{number}"


def validate_course_kc_code(code: str) -> None:
    if not KC_CODE_PATTERN.fullmatch(str(code or "")):
        raise ValidationError("课程 kc_code 必须采用 KC1、KC2… 格式。")


def course_kc_sort_key(code: str) -> tuple[int, str]:
    value = str(code or "")
    if KC_CODE_PATTERN.fullmatch(value):
        return int(value[2:]), value
    return 2**63 - 1, value


def allocate_next_course_kc_code(*, course_id: int) -> str:
    from apps.accounts.models import Course

    from ..models import Concept

    Course.objects.select_for_update().only("pk").get(pk=course_id)
    highest_sequence = 0
    for existing_code in Concept.objects.filter(course_id=course_id).values_list(
        "code", flat=True
    ):
        if KC_CODE_PATTERN.fullmatch(existing_code or ""):
            highest_sequence = max(highest_sequence, int(existing_code[2:]))
    return f"KC{highest_sequence + 1}"


def _damerau_levenshtein(left: str, right: str) -> int:
    rows = len(left) + 1
    columns = len(right) + 1
    distance = [[0] * columns for _ in range(rows)]
    for row in range(rows):
        distance[row][0] = row
    for column in range(columns):
        distance[0][column] = column
    for row in range(1, rows):
        for column in range(1, columns):
            cost = 0 if left[row - 1] == right[column - 1] else 1
            distance[row][column] = min(
                distance[row - 1][column] + 1,
                distance[row][column - 1] + 1,
                distance[row - 1][column - 1] + cost,
            )
            if (
                row > 1
                and column > 1
                and left[row - 1] == right[column - 2]
                and left[row - 2] == right[column - 1]
            ):
                distance[row][column] = min(
                    distance[row][column],
                    distance[row - 2][column - 2] + 1,
                )
    return distance[-1][-1]


def resolve_course_kc_reference(*, course, token: str) -> CourseKCResolution:
    from ..models import Concept

    raw_token = str(token or "").strip()
    normalized_code = unicodedata.normalize("NFKC", raw_token).strip().upper()
    if KC_CODE_PATTERN.fullmatch(normalized_code):
        concept = Concept.objects.filter(course=course, code=normalized_code).first()
        if concept is None:
            raise CourseKCResolutionError(
                "kc_not_found",
                f"课程 KC 总库中不存在 {raw_token}",
            )
        if concept.status == Concept.Status.ARCHIVED:
            raise CourseKCResolutionError("kc_archived", f"{concept.code} 已归档")
        return CourseKCResolution(
            concept,
            raw_token,
            normalized_code,
            "kc_code_exact",
        )

    normalized_name = normalize_course_kc_name(raw_token)
    if not normalized_name:
        raise CourseKCResolutionError("kc_not_found", "KC 引用不能为空")
    exact = Concept.objects.filter(
        course=course,
        normalized_name=normalized_name,
    ).first()
    if exact is not None:
        if exact.status == Concept.Status.ARCHIVED:
            raise CourseKCResolutionError("kc_archived", f"{exact.code} 已归档")
        return CourseKCResolution(
            exact,
            raw_token,
            normalized_name,
            "name_exact",
        )

    maximum_distance = 1 if len(normalized_name) < 8 else 2
    candidates = []
    for concept in Concept.objects.filter(course=course, status=Concept.Status.ACTIVE):
        distance = _damerau_levenshtein(normalized_name, concept.normalized_name)
        if distance <= maximum_distance:
            candidates.append((distance, concept))
    if not candidates:
        raise CourseKCResolutionError(
            "kc_not_found",
            f"课程 KC 总库中无法识别：{raw_token}",
        )
    nearest_distance = min(distance for distance, _concept in candidates)
    nearest = [
        concept for distance, concept in candidates if distance == nearest_distance
    ]
    if len(nearest) != 1:
        codes = "、".join(sorted(concept.code for concept in nearest))
        raise CourseKCResolutionError(
            "kc_ambiguous",
            f"KC 引用 {raw_token} 存在同距候选：{codes}",
        )
    return CourseKCResolution(
        nearest[0],
        raw_token,
        normalized_name,
        "edit_distance",
        nearest_distance,
    )


class CourseKCService:
    @staticmethod
    def validate_unique_name(*, course, name: str, exclude_pk=None) -> str:
        from ..models import Concept

        normalized = normalize_course_kc_name(name)
        if not normalized:
            raise ValidationError("KC 名称不能为空。")
        duplicate = Concept.objects.filter(
            course=course,
            normalized_name=normalized,
        )
        if exclude_pk is not None:
            duplicate = duplicate.exclude(pk=exclude_pk)
        if duplicate.exists():
            raise ValidationError("当前课程已存在规范化后同名的 KC。")
        return normalized

    @classmethod
    @transaction.atomic
    def create(cls, *, course, sequence: int, name: str, description: str = ""):
        from ..models import Concept

        code = course_kc_code(sequence)
        cls.validate_unique_name(course=course, name=name)
        if Concept.objects.filter(course=course, code=code).exists():
            raise ValidationError(f"当前课程已存在 {code}。")
        return Concept.objects.create(
            course=course,
            code=code,
            name=" ".join(str(name).strip().split()),
            description=description,
        )

    @classmethod
    @transaction.atomic
    def rename(cls, *, concept, name: str, description: str = ""):
        cls.validate_unique_name(
            course=concept.course,
            name=name,
            exclude_pk=concept.pk,
        )
        concept.name = " ".join(str(name).strip().split())
        concept.description = description
        concept.save(update_fields=["name", "description", "normalized_name"])
        return concept
