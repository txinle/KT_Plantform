"""Read-only diagnostics for the historical class-experiment task backfill.

This module deliberately reports only aggregate facts.  It never derives a
class from a current roster, and it never returns a student identity, answer,
event document, or model ``__dict__``.
"""

from collections import defaultdict
from dataclasses import dataclass

from django.db.models import Q

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
class _ScopedCounts:
    participant_count: int
    session_count: int
    attempt_count: int
    grade_record_count: int


def _course_label(course):
    if course is None:
        return ""
    parts = [part for part in (course.code, course.name, course.term) if part]
    return " · ".join(parts)


def _class_label(class_group, fallback=""):
    if class_group is None:
        return fallback
    return f"{class_group.academic_year} {class_group.name}".strip()


def _scope_filter(snapshot_pairs):
    condition = Q()
    for version_id, class_snapshot_id in snapshot_pairs:
        condition |= Q(version_id=version_id, class_snapshot_id=class_snapshot_id)
    return condition


def _scoped_counts(snapshots):
    """Count only records explicitly bound to a version × class snapshot pair."""

    pairs = tuple(
        sorted(
            {(snapshot.version_id, snapshot.pk) for snapshot in snapshots},
            key=lambda pair: (pair[0], str(pair[1])),
        )
    )
    if not pairs:
        return _ScopedCounts(0, 0, 0, 0)

    pair_filter = _scope_filter(pairs)
    participant_count = ExperimentVersionParticipantSnapshot.objects.filter(
        pair_filter
    ).count()
    scoped_sessions = ExperimentSession.objects.filter(pair_filter)
    session_count = scoped_sessions.count()
    scoped_attempts = Attempt.objects.filter(
        session_id__in=scoped_sessions.values("pk")
    )
    attempt_count = scoped_attempts.count()
    grade_record_count = GradeRecord.objects.filter(
        submission__attempt_id__in=scoped_attempts.values("pk")
    ).count()
    return _ScopedCounts(
        participant_count=participant_count,
        session_count=session_count,
        attempt_count=attempt_count,
        grade_record_count=grade_record_count,
    )


def _ordered_reason_codes(reason_codes):
    reason_set = set(reason_codes)
    return tuple(code for code in REASON_CODE_ORDER if code in reason_set)


def _row(*, course, class_group, experiment, class_label, snapshots, reason_codes):
    counts = _scoped_counts(snapshots)
    return ClassExperimentTaskAuditRow(
        course_id=course.pk if course is not None else None,
        course_label=_course_label(course),
        class_group_id=class_group.pk if class_group is not None else None,
        class_course_id=(class_group.course_id if class_group is not None else None),
        class_label=_class_label(class_group, fallback=class_label),
        experiment_id=experiment.pk if experiment is not None else None,
        experiment_label=experiment.name if experiment is not None else "",
        release_snapshot_count=len({snapshot.version_id for snapshot in snapshots}),
        class_snapshot_count=len(snapshots),
        participant_count=counts.participant_count,
        session_count=counts.session_count,
        attempt_count=counts.attempt_count,
        grade_record_count=counts.grade_record_count,
        reason_codes=_ordered_reason_codes(reason_codes),
    )


def _task_is_aligned(task, *, class_group, experiment, snapshot):
    return (
        task.class_group_id == class_group.pk
        and task.experiment_id == experiment.pk
        and class_group.course_id == experiment.course_id
        and task.release_snapshot_id == snapshot.version_id
        and task.class_snapshot_id == snapshot.pk
        and task.release_snapshot.experiment_id == experiment.pk
        and task.class_snapshot.version_id == snapshot.version_id
        and task.class_snapshot.source_class_group_id == class_group.pk
        and task.published_at == snapshot.version.published_at
    )


def audit_class_experiment_tasks():
    """Return stable, aggregate-only rows for every unresolved task identity.

    A complete, aligned formal task is intentionally omitted.  All conflict
    rows remain visible, including legacy class labels that no longer map to a
    current ``ClassGroup``.
    """

    snapshots = list(
        ExperimentClassSnapshot.objects.select_related(
            "version__experiment__course",
            "source_class_group__course",
        ).order_by(
            "version__experiment_id",
            "source_class_group_id",
            "version_id",
            "class_snapshot_id",
        )
    )
    tasks = list(
        ClassExperimentTask.objects.select_related(
            "class_group__course",
            "experiment__course",
            "release_snapshot__experiment",
            "class_snapshot__version",
        ).order_by("class_group_id", "experiment_id", "pk")
    )

    snapshots_by_pair = defaultdict(list)
    source_missing_snapshots = []
    for snapshot in snapshots:
        if snapshot.source_class_group_id is None:
            source_missing_snapshots.append(snapshot)
            continue
        snapshots_by_pair[
            (snapshot.source_class_group_id, snapshot.version.experiment_id)
        ].append(snapshot)

    tasks_by_pair = defaultdict(list)
    for task in tasks:
        tasks_by_pair[(task.class_group_id, task.experiment_id)].append(task)

    ordered_rows = []

    for snapshot in source_missing_snapshots:
        experiment = snapshot.version.experiment
        course = experiment.course
        row = _row(
            course=course,
            class_group=None,
            experiment=experiment,
            class_label=snapshot.display_name,
            snapshots=[snapshot],
            reason_codes=("source_class_missing",),
        )
        ordered_rows.append(
            (
                (
                    course.pk if course is not None else -1,
                    1,
                    snapshot.display_name,
                    experiment.pk,
                    snapshot.version_id,
                    str(snapshot.pk),
                ),
                row,
            )
        )

    for pair in sorted(set(snapshots_by_pair) | set(tasks_by_pair)):
        class_group_id, experiment_id = pair
        pair_snapshots = sorted(
            snapshots_by_pair.get(pair, []),
            key=lambda snapshot: (snapshot.version_id, str(snapshot.pk)),
        )
        pair_tasks = tasks_by_pair.get(pair, [])

        if pair_snapshots:
            first_snapshot = pair_snapshots[0]
            class_group = first_snapshot.source_class_group
            experiment = first_snapshot.version.experiment
            course = experiment.course
            version_ids = {snapshot.version_id for snapshot in pair_snapshots}
            reasons = []

            if class_group.course_id != experiment.course_id:
                reasons.append("course_mismatch")
            if len(version_ids) != 1:
                reasons.append("multiple_release_snapshots")

            expected_snapshot = None
            if len(version_ids) == 1:
                version_id = next(iter(version_ids))
                matching_snapshots = [
                    snapshot
                    for snapshot in pair_snapshots
                    if snapshot.version_id == version_id
                ]
                if len(matching_snapshots) != 1:
                    reasons.append("snapshot_count_mismatch")
                else:
                    expected_snapshot = matching_snapshots[0]

            if pair_tasks:
                if (
                    len(pair_tasks) != 1
                    or expected_snapshot is None
                    or not _task_is_aligned(
                        pair_tasks[0],
                        class_group=class_group,
                        experiment=experiment,
                        snapshot=expected_snapshot,
                    )
                ):
                    reasons.append("task_alignment_mismatch")
            elif not reasons:
                reasons.append("missing_eligible_task")

            if reasons:
                row = _row(
                    course=course,
                    class_group=class_group,
                    experiment=experiment,
                    class_label="",
                    snapshots=pair_snapshots,
                    reason_codes=reasons,
                )
                ordered_rows.append(
                    (
                        (
                            course.pk if course is not None else -1,
                            0,
                            class_group.pk,
                            experiment.pk,
                            min(version_ids),
                            "",
                        ),
                        row,
                    )
                )
            continue

        # A task can itself be the only remaining evidence of a broken link.
        # It has no trustworthy snapshot scope, so all fact counts stay zero.
        task = pair_tasks[0]
        class_group = task.class_group
        experiment = task.experiment
        course = experiment.course
        task_only_reasons = ["snapshot_count_mismatch", "task_alignment_mismatch"]
        if class_group.course_id != experiment.course_id:
            task_only_reasons.append("course_mismatch")
        row = _row(
            course=course,
            class_group=class_group,
            experiment=experiment,
            class_label="",
            snapshots=[],
            reason_codes=task_only_reasons,
        )
        ordered_rows.append(
            (
                (
                    course.pk if course is not None else -1,
                    0,
                    class_group.pk,
                    experiment.pk,
                    -1,
                    "",
                ),
                row,
            )
        )

    return tuple(row for _sort_key, row in sorted(ordered_rows, key=lambda item: item[0]))
