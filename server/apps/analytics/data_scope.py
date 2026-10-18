"""One validated, serializable scope for every data-center read path."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone as datetime_timezone
import re
from typing import Mapping
from uuid import UUID
from zoneinfo import ZoneInfo

from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.accounts.models import ClassGroup, CourseEnrollment, CourseSubject, User
from apps.assessment.models import AssessmentEvent, Attempt, ExperimentSession
from apps.experiments.models import (
    Condition,
    Experiment,
    ExperimentClassSnapshot,
    ExperimentStudent,
    ExperimentVersion,
)


class ScopeValidationError(ValueError):
    """The object is in this course, but the supplied filters contradict."""


class ScopeNotFound(LookupError):
    """The supplied identifier is not visible inside the current course."""


def normalize_scope_mapping(course, values: Mapping | None = None):
    """Normalize compatibility query parameters into the v1 scope contract.

    Historical links used mutable class labels and student numbers.  They are
    accepted only as a one-time lookup to an immutable identifier; no query in
    the data center is allowed to keep filtering on the display string.
    """
    values = values or {}
    normalized = dict(values.items())

    legacy_status = _optional_value(normalized, "status")
    if legacy_status and not (
        _optional_value(normalized, "session_status")
        or _optional_value(normalized, "attempt_status")
    ):
        session_statuses = {value for value, _label in ExperimentSession.Status.choices}
        attempt_statuses = {value for value, _label in Attempt.Status.choices}
        if legacy_status in session_statuses and legacy_status not in attempt_statuses:
            normalized["session_status"] = legacy_status
        elif legacy_status in attempt_statuses and legacy_status not in session_statuses:
            normalized["attempt_status"] = legacy_status
        else:
            raise ScopeValidationError("旧 status 参数无法无歧义转换，请明确选择会话或作答状态")
    normalized.pop("status", None)

    legacy_class_name = _optional_value(normalized, "class_name")
    if legacy_class_name is not None and not _optional_value(normalized, "class_snapshot_id"):
        snapshots = ExperimentClassSnapshot.objects.filter(
            version__experiment__course=course
        )
        experiment_id = _optional_value(normalized, "experiment_id")
        version_id = _optional_value(normalized, "version_id")
        if experiment_id:
            snapshots = snapshots.filter(version__experiment_id=experiment_id)
        if version_id:
            snapshots = snapshots.filter(version_id=version_id)
        if legacy_class_name == "__unrecorded__":
            snapshots = snapshots.filter(capture_mode=ExperimentClassSnapshot.CaptureMode.UNRECORDED)
        else:
            snapshots = snapshots.filter(name_snapshot=legacy_class_name)
        snapshot_ids = list(snapshots.values_list("pk", flat=True)[:2])
        if len(snapshot_ids) != 1:
            raise ScopeValidationError(
                "旧班级名称无法唯一映射到历史班级快照，请在范围栏重新选择班级快照"
            )
        normalized["class_snapshot_id"] = str(snapshot_ids[0])
    normalized.pop("class_name", None)

    legacy_student_number = _optional_value(normalized, "student_number")
    if legacy_student_number is not None and not _optional_value(normalized, "student_id"):
        students = User.objects.filter(
            assessment_sessions__version__experiment__course=course,
            student_number=legacy_student_number,
        ).distinct()
        student_ids = list(students.values_list("pk", flat=True)[:2])
        if len(student_ids) != 1:
            raise ScopeValidationError(
                "旧学生学号无法唯一映射到当前课程学生，请在范围栏重新选择学生"
            )
        normalized["student_id"] = student_ids[0]
    normalized.pop("student_number", None)
    return normalized


def _optional_value(values: Mapping, key: str):
    value = values.get(key)
    return value if value not in (None, "") else None


def _optional_positive_int(values: Mapping, key: str):
    value = _optional_value(values, key)
    if value is None:
        return None
    try:
        normalized = int(value)
    except (TypeError, ValueError) as error:
        raise ScopeValidationError(f"{key} 必须是正整数") from error
    if normalized <= 0:
        raise ScopeValidationError(f"{key} 必须是正整数")
    return normalized


def _optional_uuid(values: Mapping, key: str):
    value = _optional_value(values, key)
    if value is None:
        return None
    try:
        return UUID(str(value))
    except (TypeError, ValueError, AttributeError) as error:
        raise ScopeValidationError(f"{key} 必须是 UUID") from error


def _optional_utc_datetime(values: Mapping, key: str):
    value = _optional_value(values, key)
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = parse_datetime(str(value))
    if parsed is None:
        raise ScopeValidationError(f"{key} 必须是 ISO 8601 时间")
    if timezone.is_naive(parsed):
        raise ScopeValidationError(f"{key} 必须带时区")
    return parsed.astimezone(datetime_timezone.utc)


@dataclass(frozen=True)
class DataScope:
    """Canonical course-local filter contract.

    Every non-empty filter is applied.  Validation never silently replaces a
    supplied filter with a related object's value, which prevents a URL from
    looking narrower than the actual query.
    """

    course: object
    experiment_id: int | None = None
    version_mode: str = "all"
    version_id: int | None = None
    class_id: int | None = None
    class_snapshot_id: UUID | None = None
    student_id: int | None = None
    session_id: int | None = None
    condition_id: int | None = None
    session_status: str | None = None
    attempt_status: str | None = None
    started_from_utc: datetime | None = None
    started_before_utc: datetime | None = None
    search: str | None = None
    session_ref: str | None = None
    experiment_ids: tuple[int, ...] = ()

    SCOPE_VERSION = 1
    VERSION_MODES = frozenset({"single", "all"})

    @classmethod
    def from_mapping(cls, course, values: Mapping | None = None):
        values = normalize_scope_mapping(course, values)
        scope_version = _optional_value(values, "scope_version")
        if scope_version is not None and str(scope_version) != str(cls.SCOPE_VERSION):
            raise ScopeValidationError("不支持的数据范围契约版本")
        version_id = _optional_positive_int(values, "version_id")
        raw_version_mode = _optional_value(values, "version_mode")
        version_mode = raw_version_mode or ("single" if version_id else "all")
        if version_mode not in cls.VERSION_MODES:
            raise ScopeValidationError("version_mode 必须为 single 或 all")
        if version_mode == "single" and version_id is None:
            raise ScopeValidationError("单版本范围必须指定 version_id")
        if version_mode == "all" and version_id is not None:
            raise ScopeValidationError("全版本范围不能指定 version_id")

        raw_experiments = values.get("experiment_ids") or ()
        if isinstance(raw_experiments, str):
            raw_experiments = raw_experiments.split(",")
        try:
            experiment_ids = tuple(sorted({int(value) for value in raw_experiments}))
        except (TypeError, ValueError) as error:
            raise ScopeValidationError("实验范围必须为实验编号列表") from error
        if any(value <= 0 for value in experiment_ids):
            raise ScopeValidationError("实验编号必须为正整数")
        single_experiment = _optional_positive_int(values, "experiment_id")
        if experiment_ids and single_experiment and experiment_ids != (single_experiment,):
            raise ScopeValidationError("单实验参数与所选实验范围不一致")
        scope = cls(
            course=course,
            experiment_id=_optional_positive_int(values, "experiment_id"),
            version_mode=version_mode,
            version_id=version_id,
            class_id=_optional_positive_int(values, "class_id"),
            class_snapshot_id=_optional_uuid(values, "class_snapshot_id"),
            student_id=_optional_positive_int(values, "student_id"),
            session_id=_optional_positive_int(values, "session_id"),
            condition_id=_optional_positive_int(values, "condition_id"),
            session_status=_optional_value(values, "session_status"),
            attempt_status=_optional_value(values, "attempt_status"),
            started_from_utc=_optional_utc_datetime(values, "started_from_utc"),
            started_before_utc=_optional_utc_datetime(values, "started_before_utc"),
            search=(_optional_value(values, "search") or "").strip()[:100] or None,
            session_ref=_optional_value(values, "session_ref"),
            experiment_ids=experiment_ids,
        )
        scope._validate()
        return scope

    def _validate(self):
        if self.session_status and self.session_status not in {
            value for value, _label in ExperimentSession.Status.choices
        }:
            raise ScopeValidationError("session_status 无效")
        if self.attempt_status and self.attempt_status not in {
            value for value, _label in Attempt.Status.choices
        }:
            raise ScopeValidationError("attempt_status 无效")
        if (
            self.started_from_utc is not None
            and self.started_before_utc is not None
            and self.started_from_utc >= self.started_before_utc
        ):
            raise ScopeValidationError("开始时间范围不能为空或倒置")

        experiment_ids = set(self.experiment_ids)
        if self.experiment_id is not None:
            experiment_ids.add(self.experiment_id)
        experiments = {
            experiment.pk: experiment
            for experiment in Experiment.objects.filter(course=self.course, pk__in=experiment_ids)
        } if experiment_ids else {}
        if experiments.keys() != experiment_ids:
            raise ScopeNotFound("实验不属于当前课程")
        experiment = experiments.get(self.experiment_id)
        version = self._course_version(self.version_id)
        class_group = self._course_class_group(self.class_id)
        class_snapshot = self._course_class_snapshot(self.class_snapshot_id)
        condition = self._course_condition(self.condition_id)
        session = self._course_session(self.session_id)
        if self.session_ref:
            referenced_id = self._referenced_session_id()
            referenced = session if session and session.pk == referenced_id else self._course_session(referenced_id)
            if referenced.started_at is None or self._session_reference(referenced.pk, referenced.started_at) != self.session_ref:
                raise ScopeNotFound("会话不属于当前课程")
            if session and session.pk != referenced_id:
                raise ScopeValidationError("会话编号与会话标识不一致")
            session = referenced
        self._validate_course_student(self.student_id)

        if self.experiment_ids:
            for selected_experiment in (version.experiment_id if version else None,
                    class_snapshot.version.experiment_id if class_snapshot else None,
                    condition.experiment_id if condition else None):
                if selected_experiment and selected_experiment not in self.experiment_ids:
                    raise ScopeValidationError("历史范围不属于所选实验")

        if experiment and version and version.experiment_id != experiment.pk:
            raise ScopeValidationError("实验与版本不属于同一范围")
        if experiment and class_snapshot and class_snapshot.version.experiment_id != experiment.pk:
            raise ScopeValidationError("实验与班级快照不属于同一范围")
        if class_group and class_snapshot and class_snapshot.source_class_group_id != class_group.pk:
            raise ScopeValidationError("课程班级与班级快照不属于同一范围")
        if version and class_snapshot and class_snapshot.version_id != version.pk:
            raise ScopeValidationError("版本与班级快照不属于同一范围")
        if experiment and condition and condition.experiment_id != experiment.pk:
            raise ScopeValidationError("实验与条件不属于同一范围")
        if version and condition and condition.experiment_id != version.experiment_id:
            raise ScopeValidationError("版本与条件不属于同一范围")

        if session:
            if self.experiment_ids and session.version.experiment_id not in self.experiment_ids:
                raise ScopeValidationError("会话不属于所选实验范围")
            if experiment and session.version.experiment_id != experiment.pk:
                raise ScopeValidationError("实验与会话不属于同一范围")
            if version and session.version_id != version.pk:
                raise ScopeValidationError("版本与会话不属于同一范围")
            if class_snapshot and session.class_snapshot_id != class_snapshot.pk:
                raise ScopeValidationError("班级快照与会话不属于同一范围")
            if class_group and (
                session.class_snapshot is None
                or session.class_snapshot.source_class_group_id != class_group.pk
            ):
                raise ScopeValidationError("课程班级与会话不属于同一范围")
            if self.student_id and session.student_id != self.student_id:
                raise ScopeValidationError("学生与会话不属于同一范围")
            if condition and session.condition_id != condition.pk:
                raise ScopeValidationError("条件与会话不属于同一范围")

    def _course_version(self, version_id):
        if version_id is None:
            return None
        version = (
            ExperimentVersion.objects.select_related("experiment__course")
            .filter(pk=version_id)
            .first()
        )
        if version is None or version.experiment.course_id != self.course.pk:
            raise ScopeNotFound("实验版本不属于当前课程")
        return version

    def _course_class_snapshot(self, class_snapshot_id):
        if class_snapshot_id is None:
            return None
        snapshot = (
            ExperimentClassSnapshot.objects.select_related("version__experiment")
            .filter(pk=class_snapshot_id)
            .first()
        )
        if snapshot is None or snapshot.version.experiment.course_id != self.course.pk:
            raise ScopeNotFound("班级快照不属于当前课程")
        return snapshot

    def _course_class_group(self, class_id):
        if class_id is None:
            return None
        class_group = ClassGroup.objects.filter(pk=class_id).first()
        if class_group is None or class_group.course_id != self.course.pk:
            raise ScopeNotFound("班级不属于当前课程")
        return class_group

    def _course_condition(self, condition_id):
        if condition_id is None:
            return None
        condition = (
            Condition.objects.select_related("experiment__course")
            .filter(pk=condition_id)
            .first()
        )
        if condition is None or condition.experiment.course_id != self.course.pk:
            raise ScopeNotFound("实验条件不属于当前课程")
        return condition

    def _course_session(self, session_id):
        if session_id is None:
            return None
        session = (
            ExperimentSession.objects.select_related("version__experiment", "class_snapshot")
            .filter(pk=session_id, version__experiment__course=self.course)
            .first()
        )
        if session is None or session.version.experiment.course_id != self.course.pk:
            raise ScopeNotFound("会话不属于当前课程")
        return session

    def _validate_course_student(self, student_id):
        if student_id is None:
            return
        if not User.objects.filter(pk=student_id, role=User.Role.STUDENT).exists():
            raise ScopeNotFound("学生不属于当前课程")
        belongs_to_course = (
            CourseEnrollment.objects.filter(
                course=self.course, student_id=student_id
            ).exists()
            or CourseSubject.objects.filter(
                course=self.course, student_id=student_id
            ).exists()
            or ExperimentStudent.objects.filter(
                experiment__course=self.course, student_id=student_id
            ).exists()
            or ExperimentSession.objects.filter(
                version__experiment__course=self.course, student_id=student_id
            ).exists()
        )
        if not belongs_to_course:
            raise ScopeNotFound("学生不属于当前课程")

    def sessions(self):
        filters = {"version__experiment__course_id": self.course.pk}
        if self.experiment_id is not None:
            filters["version__experiment_id"] = self.experiment_id
        if self.experiment_ids:
            filters["version__experiment_id__in"] = self.experiment_ids
        if self.version_id is not None:
            filters["version_id"] = self.version_id
        if self.class_id is not None:
            filters["class_snapshot__source_class_group_id"] = self.class_id
        if self.class_snapshot_id is not None:
            filters["class_snapshot_id"] = self.class_snapshot_id
        if self.student_id is not None:
            filters["student_id"] = self.student_id
        if self.session_id is not None:
            filters["pk"] = self.session_id
        if self.session_ref:
            filters["pk"] = self._referenced_session_id()
        if self.condition_id is not None:
            filters["condition_id"] = self.condition_id
        if self.session_status is not None:
            filters["status"] = self.session_status
        if self.started_from_utc is not None:
            filters["started_at__gte"] = self.started_from_utc
        if self.started_before_utc is not None:
            filters["started_at__lt"] = self.started_before_utc
        queryset = ExperimentSession.objects.filter(**filters, started_at__isnull=False)
        if self.search:
            selected = []
            for session in queryset.select_related("student"):
                reference = self._session_reference(session.pk, session.started_at)
                if self.search.casefold() not in " ".join(
                    (session.student.student_number or "", session.student.shown_name, reference)
                ).casefold():
                    continue
                selected.append(session.pk)
            queryset = queryset.filter(pk__in=selected)
        return queryset

    def session_references(self):
        return {
            str(pk): self._session_reference(pk, started)
            for pk, started in ExperimentSession.objects.filter(version__experiment__course=self.course, started_at__isnull=False)
            .values_list("pk", "started_at")
        }

    @staticmethod
    def _session_reference(pk, started):
        return f"S{started.astimezone(ZoneInfo('Asia/Shanghai')):%Y%m%d}_{pk:04d}"

    def _referenced_session_id(self):
        match = re.fullmatch(r"S\d{8}_([0-9]{4,19})", str(self.session_ref))
        if match is None or int(match[1]) <= 0:
            raise ScopeNotFound("会话不属于当前课程")
        return int(match[1])

    @property
    def is_complete(self):
        return bool((self.experiment_id or self.experiment_ids) and (self.class_id or self.class_snapshot_id))

    def attempts(self):
        attempts = Attempt.objects.filter(session__in=self.sessions())
        if self.attempt_status is not None:
            attempts = attempts.filter(status=self.attempt_status)
        return attempts

    def events(self):
        events = AssessmentEvent.objects.filter(session__in=self.sessions())
        if self.attempt_status is not None:
            events = events.filter(attempt__in=self.attempts())
        return events

    @property
    def is_single_version(self):
        return self.version_mode == "single" and self.version_id is not None

    def serialize(self):
        values = {"scope_version": self.SCOPE_VERSION}
        if self.experiment_ids:
            values["experiment_ids"] = list(self.experiment_ids)
        for key, value in (
            ("experiment_id", self.experiment_id),
            ("version_mode", self.version_mode),
            ("version_id", self.version_id),
            ("class_id", self.class_id),
            ("class_snapshot_id", self.class_snapshot_id),
            ("student_id", self.student_id),
            ("session_id", self.session_id),
            ("condition_id", self.condition_id),
            ("session_status", self.session_status),
            ("attempt_status", self.attempt_status),
            ("started_from_utc", self.started_from_utc),
            ("started_before_utc", self.started_before_utc),
            ("search", self.search),
            ("session_ref", self.session_ref),
        ):
            if value is None:
                continue
            if isinstance(value, datetime):
                values[key] = value.astimezone(datetime_timezone.utc).isoformat()
            elif isinstance(value, UUID):
                values[key] = str(value)
            else:
                values[key] = value
        return values
