"""Teacher-facing experiment-and-class scope.

The complete research scope remains in ``data_scope.py``.  This adapter keeps
research identifiers out of ordinary teaching navigation while still reading
sessions through immutable class snapshots.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from apps.accounts.models import ClassGroup, CourseEnrollment
from apps.assessment.models import AssessmentEvent, Attempt, ExperimentSession
from apps.experiments.models import Experiment

from .data_scope import ScopeNotFound, ScopeValidationError, _optional_positive_int


@dataclass(frozen=True)
class TeachingScope:
    course: object
    experiment_id: int | None = None
    class_id: int | None = None

    @classmethod
    def from_mapping(cls, course, values: Mapping | None = None):
        values = values or {}
        scope = cls(
            course=course,
            experiment_id=_optional_positive_int(values, "experiment_id"),
            class_id=_optional_positive_int(values, "class_id"),
        )
        scope._validate()
        return scope

    def _validate(self):
        if self.experiment_id is not None and not Experiment.objects.filter(
            pk=self.experiment_id,
            course=self.course,
        ).exists():
            raise ScopeNotFound("实验不属于当前课程")
        if self.class_id is not None and not ClassGroup.objects.filter(
            pk=self.class_id,
            course=self.course,
            is_active=True,
        ).exists():
            raise ScopeNotFound("班级不属于当前课程或已停用")
        if self.class_id is not None and self.experiment_id is None:
            raise ScopeValidationError("选择班级前必须先选择实验")

    def roster(self):
        if self.class_id is None:
            return CourseEnrollment.objects.none()
        return (
            CourseEnrollment.objects.filter(
                course=self.course,
                class_group_id=self.class_id,
                is_active=True,
                student__is_active=True,
            )
            .select_related("student")
            .order_by("student__student_number", "student__display_name", "student_id")
        )

    def sessions(self):
        sessions = ExperimentSession.objects.filter(
            version__experiment__course=self.course,
        )
        if self.experiment_id is not None:
            sessions = sessions.filter(version__experiment_id=self.experiment_id)
        if self.class_id is not None:
            sessions = sessions.filter(
                class_snapshot__source_class_group_id=self.class_id
            )
        return sessions

    def attempts(self):
        return Attempt.objects.filter(session__in=self.sessions())

    def events(self):
        return AssessmentEvent.objects.filter(session__in=self.sessions())

    def serialize(self):
        values = {}
        if self.experiment_id is not None:
            values["experiment_id"] = self.experiment_id
        if self.class_id is not None:
            values["class_id"] = self.class_id
        return values

    @property
    def is_complete(self):
        return self.experiment_id is not None and self.class_id is not None
