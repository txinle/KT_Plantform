"""Read-only diagnostics for the historical class-experiment task backfill.

The audit deliberately moves only scalar, allow-listed facts through Python.
It never derives a class from a current roster and never returns student,
answer, event, or model ``__dict__`` data.
"""

from collections import defaultdict
from dataclasses import dataclass
from itertools import groupby

from django.db.models import Count

from apps.assessment.models import Attempt, ExperimentSession, GradeRecord

from .models import (
    ClassExperimentTask,
    ExperimentClassSnapshot,
    ExperimentVersionParticipantSnapshot,
)


REASON_CODE_ORDER = (
    "source_class_missing",
    "multiple_release_snapshots",
    "course_mismatch",
    "snapshot_count_mismatch",
    "missing_eligible_task",
    "task_alignment_mismatch",
)


@dataclass(frozen=True)
class ClassExperimentTaskAuditRow:
    """The allow-listed payload available to the management command."""

    course_id: int | None
    course_label: str
    class_group_id: int | None
    class_course_id: int | None
    class_label: str
    experiment_id: int | None
    experiment_label: str
    release_snapshot_count: int
    class_snapshot_count: int
    participant_count: int
    session_count: int
    attempt_count: int
    grade_record_count: int
    reason_codes: tuple[str, ...]

    def as_dict(self):
        """Return only this DTO's public, non-student aggregate fields."""

        return {
            "course_id": self.course_id,
            "course_label": self.course_label,
            "class_group_id": self.class_group_id,
            "class_course_id": self.class_course_id,
            "class_label": self.class_label,
            "experiment_id": self.experiment_id,
            "experiment_label": self.experiment_label,
            "release_snapshot_count": self.release_snapshot_count,
            "class_snapshot_count": self.class_snapshot_count,
            "participant_count": self.participant_count,
            "session_count": self.session_count,
            "attempt_count": self.attempt_count,
            "grade_record_count": self.grade_record_count,
            "reason_codes": list(self.reason_codes),
        }


@dataclass(frozen=True)
class _SnapshotFact:
    class_snapshot_id: object
    version_id: int
    release_published_at: object
    class_group_id: int | None
    class_course_id: int | None
    class_label: str
    course_id: int | None
    course_label: str
    experiment_id: int | None
    experiment_label: str


@dataclass(frozen=True)
class _TaskFact:
    class_group_id: int
    class_course_id: int | None
    class_label: str
    experiment_id: int
    experiment_label: str
    course_id: int | None
    course_label: str
    release_snapshot_id: int
    release_experiment_id: int
    release_published_at: object
    class_snapshot_id: object
    class_snapshot_version_id: int
    class_snapshot_source_class_group_id: int | None
    published_at: object


@dataclass(frozen=True)
class _ScopedCounts:
    participant_count: int
    session_count: int
    attempt_count: int
    grade_record_count: int


def _joined_label(*parts):
    return " ".join(str(part) for part in parts if part).strip()


def _course_label(code, name, term):
    return " · ".join(str(part) for part in (code, name, term) if part)


def _snapshot_fact(values):
    class_group_id = values["source_class_group_id"]
    return _SnapshotFact(
        class_snapshot_id=values["class_snapshot_id"],
        version_id=values["version_id"],
        release_published_at=values["version__published_at"],
        class_group_id=class_group_id,
        class_course_id=values["source_class_group__course_id"],
        class_label=_joined_label(
            values["source_class_group__academic_year"]
            if class_group_id is not None
            else values["academic_year_snapshot"],
            values["source_class_group__name"]
            if class_group_id is not None
            else values["name_snapshot"],
        ),
        course_id=values["version__experiment__course_id"],
        course_label=_course_label(
            values["version__experiment__course__code"],
            values["version__experiment__course__name"],
            values["version__experiment__course__term"],
        ),
        experiment_id=values["version__experiment_id"],
        experiment_label=values["version__experiment__name"],
    )


def _task_fact(values):
    return _TaskFact(
        class_group_id=values["class_group_id"],
        class_course_id=values["class_group__course_id"],
        class_label=_joined_label(
            values["class_group__academic_year"],
            values["class_group__name"],
        ),
        experiment_id=values["experiment_id"],
        experiment_label=values["experiment__name"],
        course_id=values["experiment__course_id"],
        course_label=_course_label(
            values["experiment__course__code"],
            values["experiment__course__name"],
            values["experiment__course__term"],
        ),
        release_snapshot_id=values["release_snapshot_id"],
        release_experiment_id=values["release_snapshot__experiment_id"],
        release_published_at=values["release_snapshot__published_at"],
        class_snapshot_id=values["class_snapshot_id"],
        class_snapshot_version_id=values["class_snapshot__version_id"],
        class_snapshot_source_class_group_id=values[
            "class_snapshot__source_class_group_id"
        ],
        published_at=values["published_at"],
    )


def _ordered_reason_codes(reason_codes):
    reason_set = set(reason_codes)
    return tuple(code for code in REASON_CODE_ORDER if code in reason_set)


def _grouped_count_map(queryset, version_field, class_snapshot_field):
    """Return exact version × class-snapshot counts with one aggregate query."""

    return {
        (values[version_field], values[class_snapshot_field]): values["record_count"]
        for values in (
            queryset.values(version_field, class_snapshot_field)
            .annotate(record_count=Count("pk"))
            .order_by(version_field, class_snapshot_field)
            .iterator()
        )
    }


def _count_maps():
    """Build the four exact-pair aggregate maps used by every audit row."""

    return (
        _grouped_count_map(
            ExperimentVersionParticipantSnapshot.objects,
            "version_id",
            "class_snapshot_id",
        ),
        _grouped_count_map(
            ExperimentSession.objects,
            "version_id",
            "class_snapshot_id",
        ),
        _grouped_count_map(
            Attempt.objects,
            "session__version_id",
            "session__class_snapshot_id",
        ),
        _grouped_count_map(
            GradeRecord.objects,
            "submission__attempt__session__version_id",
            "submission__attempt__session__class_snapshot_id",
        ),
    )


def _scoped_counts(snapshots, count_maps):
    """Sum only facts whose *actual* version × snapshot key is in scope."""

    pairs = {(snapshot.version_id, snapshot.class_snapshot_id) for snapshot in snapshots}
    participant_map, session_map, attempt_map, grade_record_map = count_maps
    return _ScopedCounts(
        participant_count=sum(participant_map.get(pair, 0) for pair in pairs),
        session_count=sum(session_map.get(pair, 0) for pair in pairs),
        attempt_count=sum(attempt_map.get(pair, 0) for pair in pairs),
        grade_record_count=sum(grade_record_map.get(pair, 0) for pair in pairs),
    )


def _row(*, context, snapshots, reason_codes, count_maps):
    counts = _scoped_counts(snapshots, count_maps)
    return ClassExperimentTaskAuditRow(
        course_id=context.course_id,
        course_label=context.course_label,
        class_group_id=context.class_group_id,
        class_course_id=context.class_course_id,
        class_label=context.class_label,
        experiment_id=context.experiment_id,
        experiment_label=context.experiment_label,
        release_snapshot_count=len({snapshot.version_id for snapshot in snapshots}),
        class_snapshot_count=len(snapshots),
        participant_count=counts.participant_count,
        session_count=counts.session_count,
        attempt_count=counts.attempt_count,
        grade_record_count=counts.grade_record_count,
        reason_codes=_ordered_reason_codes(reason_codes),
    )


def _task_is_aligned(task, *, context, pair_snapshots):
    """Validate a task's own immutable links independently of candidate scope.

    Ambiguous candidate snapshots are reported separately.  A task can still
    be internally sound when the pair later gains another release or duplicate
    historical class snapshot.
    """

    pair_snapshot_ids = {snapshot.class_snapshot_id for snapshot in pair_snapshots}
    return (
        task.class_group_id == context.class_group_id
        and task.experiment_id == context.experiment_id
        and task.class_course_id == context.class_course_id
        and task.class_course_id == task.course_id == context.course_id
        and task.class_snapshot_id in pair_snapshot_ids
        and task.release_experiment_id == context.experiment_id
        and task.class_snapshot_version_id == task.release_snapshot_id
        and task.class_snapshot_source_class_group_id == context.class_group_id
        and task.published_at == task.release_published_at
    )


def _snapshot_facts():
    """Yield ordered scalar snapshot facts without hydrating model instances."""

    snapshots = (
        ExperimentClassSnapshot.objects.order_by(
            "source_class_group_id",
            "version__experiment_id",
            "version_id",
            "class_snapshot_id",
        )
        .values(
            "class_snapshot_id",
            "version_id",
            "version__published_at",
            "source_class_group_id",
            "source_class_group__course_id",
            "source_class_group__academic_year",
            "source_class_group__name",
            "academic_year_snapshot",
            "name_snapshot",
            "version__experiment_id",
            "version__experiment__name",
            "version__experiment__course_id",
            "version__experiment__course__code",
            "version__experiment__course__name",
            "version__experiment__course__term",
        )
        .iterator()
    )
    for values in snapshots:
        yield _snapshot_fact(values)


def _task_facts_by_pair():
    """Return only scalar task facts keyed by their formal class × experiment."""

    tasks_by_pair = defaultdict(list)
    tasks = (
        ClassExperimentTask.objects.order_by("class_group_id", "experiment_id", "pk")
        .values(
            "class_group_id",
            "class_group__course_id",
            "class_group__academic_year",
            "class_group__name",
            "experiment_id",
            "experiment__name",
            "experiment__course_id",
            "experiment__course__code",
            "experiment__course__name",
            "experiment__course__term",
            "release_snapshot_id",
            "release_snapshot__experiment_id",
            "release_snapshot__published_at",
            "class_snapshot_id",
            "class_snapshot__version_id",
            "class_snapshot__source_class_group_id",
            "published_at",
        )
        .iterator()
    )
    for values in tasks:
        task = _task_fact(values)
        tasks_by_pair[(task.class_group_id, task.experiment_id)].append(task)
    return tasks_by_pair


def _source_missing_sort_key(snapshot):
    return (
        snapshot.course_id if snapshot.course_id is not None else -1,
        1,
        snapshot.class_label,
        snapshot.experiment_id if snapshot.experiment_id is not None else -1,
        snapshot.version_id,
        str(snapshot.class_snapshot_id),
    )


def _mapped_sort_key(context, version_id=-1):
    return (
        context.course_id if context.course_id is not None else -1,
        0,
        context.class_group_id if context.class_group_id is not None else -1,
        context.experiment_id if context.experiment_id is not None else -1,
        version_id,
        "",
    )


def audit_class_experiment_tasks():
    """Return stable aggregate-only rows for every unresolved task identity.

    Aligned formal tasks are intentionally omitted.  The data path is bounded:
    four grouped fact queries plus one scalar task scan and one scalar snapshot
    scan, regardless of the number of class-snapshot rows.
    """

    count_maps = _count_maps()
    tasks_by_pair = _task_facts_by_pair()
    ordered_rows = []

    for pair, grouped_snapshots in groupby(
        _snapshot_facts(),
        key=lambda snapshot: (snapshot.class_group_id, snapshot.experiment_id),
    ):
        class_group_id, experiment_id = pair
        if class_group_id is None:
            for snapshot in grouped_snapshots:
                row = _row(
                    context=snapshot,
                    snapshots=(snapshot,),
                    reason_codes=("source_class_missing",),
                    count_maps=count_maps,
                )
                ordered_rows.append((_source_missing_sort_key(snapshot), row))
            continue

        pair_snapshots = tuple(grouped_snapshots)
        context = pair_snapshots[0]
        pair_tasks = tasks_by_pair.pop((class_group_id, experiment_id), ())
        version_ids = {snapshot.version_id for snapshot in pair_snapshots}
        reasons = []

        if context.class_course_id != context.course_id:
            reasons.append("course_mismatch")
        if len(version_ids) != 1:
            reasons.append("multiple_release_snapshots")
        elif len(pair_snapshots) != 1:
            reasons.append("snapshot_count_mismatch")

        if pair_tasks:
            if len(pair_tasks) != 1 or not _task_is_aligned(
                pair_tasks[0],
                context=context,
                pair_snapshots=pair_snapshots,
            ):
                reasons.append("task_alignment_mismatch")
        elif not reasons:
            reasons.append("missing_eligible_task")

        if reasons:
            row = _row(
                context=context,
                snapshots=pair_snapshots,
                reason_codes=reasons,
                count_maps=count_maps,
            )
            ordered_rows.append(
                (_mapped_sort_key(context, min(version_ids)), row)
            )

    for pair in sorted(tasks_by_pair):
        pair_tasks = tasks_by_pair[pair]
        context = pair_tasks[0]
        reasons = ["snapshot_count_mismatch", "task_alignment_mismatch"]
        if context.class_course_id != context.course_id:
            reasons.append("course_mismatch")
        row = _row(
            context=context,
            snapshots=(),
            reason_codes=reasons,
            count_maps=count_maps,
        )
        ordered_rows.append((_mapped_sort_key(context), row))

    return tuple(row for _sort_key, row in sorted(ordered_rows, key=lambda item: item[0]))
