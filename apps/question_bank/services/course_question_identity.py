import hashlib
import json
from time import sleep
from uuid import uuid4

from django.core.exceptions import ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Max

from apps.accounts.course_access import lock_course

from ..models import CourseQuestionIdentity, Question


MAX_ALLOCATION_RETRIES = 5
COURSE_IDENTITY_MISMATCH_MESSAGE = "题目不能移动到与其永久题目标识不同的课程题库。"


class CourseQuestionRenumberingBlocked(Exception):
    def __init__(self, experiment_names, *, report=None):
        self.experiment_names = list(experiment_names)
        self.report = report
        names = "、".join(f"“{name}”" for name in self.experiment_names)
        super().__init__(f"以下正式实验已引用受影响题目，不能重排课程题号：{names}。")


def stable_course_question_code(course_id, course_sequence):
    return f"C{course_id:06d}-Q{course_sequence:06d}"


def course_question_identity_fingerprint(course_or_pk, *, through_identity_pk=None):
    course_id = getattr(course_or_pk, "pk", course_or_pk)
    identities = CourseQuestionIdentity.objects.filter(course_id=course_id)
    if through_identity_pk is not None:
        identities = identities.filter(pk__lte=through_identity_pk)
    identity_map = list(
        identities
        .order_by("course_sequence", "pk")
        .values_list("question_id", "course_sequence", "stable_code")
    )
    canonical = json.dumps(identity_map, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _retire_exports(*, course_id, question_ids, reason):
    from apps.analytics.research_deletion import retire_course_question_exports

    return retire_course_question_exports(
        course_id=course_id,
        question_ids=question_ids,
        reason=reason,
    )


def _invalidate_draft_previews(*, experiment_ids, reason):
    if not experiment_ids:
        return
    from apps.experiments.research_deletion import invalidate_draft_publication_previews

    invalidate_draft_publication_previews(
        experiment_ids=experiment_ids,
        reason=reason,
    )


def _affected_export_count(*, course_id, question_ids):
    from apps.analytics.models import ExportJob
    from apps.analytics.research_deletion import affected_export_snapshots

    snapshots = affected_export_snapshots(
        course_id=course_id,
        question_ids=question_ids,
    )
    return ExportJob.objects.filter(snapshot__in=snapshots).count()


def _renumbering_plan(identities, *, course_id, remove_question_ids):
    remove_ids = {int(value) for value in remove_question_ids}
    surviving = [
        identity
        for identity in identities
        if identity.question_id is not None and identity.question_id not in remove_ids
    ]
    mappings = []
    for new_sequence, identity in enumerate(surviving, start=1):
        new_code = stable_course_question_code(course_id, new_sequence)
        if identity.course_sequence != new_sequence or identity.stable_code != new_code:
            mappings.append(
                {
                    "identity": identity,
                    "identity_id": identity.pk,
                    "question_id": identity.question_id,
                    "old_course_sequence": identity.course_sequence,
                    "old_stable_code": identity.stable_code,
                    "new_course_sequence": new_sequence,
                    "new_stable_code": new_code,
                }
            )
    removed_identities = [
        identity
        for identity in identities
        if identity.question_id is None or identity.question_id in remove_ids
    ]
    removed_question_ids = [
        identity.question_id
        for identity in removed_identities
        if identity.question_id is not None
    ]
    affected_question_ids = list(
        dict.fromkeys(
            [*removed_question_ids, *[item["question_id"] for item in mappings]]
        )
    )
    return {
        "mappings": mappings,
        "removed_identities": removed_identities,
        "affected_question_ids": affected_question_ids,
    }


def _blocking_experiment_names(question_ids):
    if not question_ids:
        return []
    from apps.experiments.models import QuestionSnapshot

    return list(
        QuestionSnapshot.objects.filter(source_question_id__in=question_ids)
        .order_by("version__experiment__name", "version__experiment_id")
        .values_list("version__experiment__name", flat=True)
        .distinct()
    )


def _draft_experiment_ids(course_id):
    from apps.experiments.models import Experiment

    return list(
        Experiment.objects.filter(course_id=course_id, status=Experiment.Status.DRAFT)
        .order_by("pk")
        .values_list("pk", flat=True)
    )


@transaction.atomic
def renumber_course_question_identities(
    course_or_pk,
    *,
    remove_question_ids=(),
    apply=True,
    retire_exports=True,
    reason="course_question_numbers_repaired",
):
    """Plan or atomically apply dense course numbering in previous-number order."""
    course = lock_course(course_or_pk)
    identities = list(
        CourseQuestionIdentity.objects.select_for_update()
        .filter(course=course)
        .order_by("course_sequence", "pk")
    )
    plan = _renumbering_plan(
        identities,
        course_id=course.pk,
        remove_question_ids=remove_question_ids,
    )
    blocking_experiments = _blocking_experiment_names(plan["affected_question_ids"])
    report = {
        "course_id": course.pk,
        "status": "blocked" if blocking_experiments else ("applied" if apply else "dry_run"),
        "dry_run": not apply,
        "mappings": [
            {key: value for key, value in item.items() if key != "identity"}
            for item in plan["mappings"]
        ],
        "removed_identity_ids": [item.pk for item in plan["removed_identities"]],
        "affected_question_ids": plan["affected_question_ids"],
        "blocking_experiments": blocking_experiments,
        "export_count": (
            _affected_export_count(
                course_id=course.pk,
                question_ids=plan["affected_question_ids"],
            )
            if not apply and (plan["removed_identities"] or plan["mappings"])
            else 0
        ),
        "cleanup_pending": False,
        "cleanup_id": None,
    }
    if blocking_experiments:
        if apply:
            raise CourseQuestionRenumberingBlocked(blocking_experiments, report=report)
        return report
    if not apply:
        return report
    if not plan["removed_identities"] and not plan["mappings"]:
        return report

    if retire_exports:
        export_result = _retire_exports(
            course_id=course.pk,
            question_ids=plan["affected_question_ids"],
            reason=reason,
        )
        report.update(
            export_count=export_result["export_count"],
            cleanup_pending=export_result["cleanup_pending"],
            cleanup_id=export_result["cleanup_id"],
        )
    _invalidate_draft_previews(
        experiment_ids=_draft_experiment_ids(course.pk),
        reason=reason,
    )

    if plan["removed_identities"]:
        CourseQuestionIdentity.objects.filter(
            pk__in=[item.pk for item in plan["removed_identities"]]
        ).delete()
    if plan["mappings"]:
        temporary_prefix = uuid4().hex[:8]
        maximum = max((identity.course_sequence for identity in identities), default=0)
        for offset, item in enumerate(plan["mappings"], start=1):
            identity = item["identity"]
            identity.course_sequence = maximum + len(identities) + offset
            identity.stable_code = f"T{temporary_prefix}-{identity.pk}"
        CourseQuestionIdentity.objects.bulk_update(
            [item["identity"] for item in plan["mappings"]],
            ["course_sequence", "stable_code"],
        )
        for item in plan["mappings"]:
            identity = item["identity"]
            identity.course_sequence = item["new_course_sequence"]
            identity.stable_code = item["new_stable_code"]
        CourseQuestionIdentity.objects.bulk_update(
            [item["identity"] for item in plan["mappings"]],
            ["course_sequence", "stable_code"],
        )
    return report


def ensure_course_question_identity(question_or_pk):
    """Keep a saved question's identity aligned with its current bank course."""
    question_id = getattr(question_or_pk, "pk", question_or_pk)
    question = Question.objects.select_related("bank").get(pk=question_id)
    existing = CourseQuestionIdentity.objects.filter(question_id=question.pk).first()
    if existing is not None:
        if existing.course_id != question.bank.course_id:
            raise ValidationError(COURSE_IDENTITY_MISMATCH_MESSAGE)
        return existing
    if question.bank.course_id is None:
        return None
    return allocate_course_question_identity(question.bank.course_id, question)


def allocate_course_question_identity(course_or_pk, question_or_pk):
    """Assign a never-reused course sequence to a question in that course."""
    course_id = getattr(course_or_pk, "pk", course_or_pk)
    question_id = getattr(question_or_pk, "pk", question_or_pk)
    last_error = None

    for attempt in range(MAX_ALLOCATION_RETRIES):
        try:
            with transaction.atomic():
                course = lock_course(course_id)
                question = Question.objects.select_related("bank").get(pk=question_id)
                if question.bank.course_id != course.pk:
                    raise ValueError("题目必须属于指定课程。")

                existing = CourseQuestionIdentity.objects.filter(question_id=question.pk).first()
                if existing is not None:
                    if existing.course_id != course.pk:
                        raise ValueError("题目已关联到其他课程的永久题目标识。")
                    return existing

                maximum = CourseQuestionIdentity.objects.filter(course=course).aggregate(
                    maximum=Max("course_sequence")
                )["maximum"]
                course_sequence = (maximum or 0) + 1
                return CourseQuestionIdentity.objects.create(
                    course=course,
                    question=question,
                    course_sequence=course_sequence,
                    stable_code=stable_course_question_code(course.pk, course_sequence),
                )
        except IntegrityError as exc:
            last_error = exc
            existing = CourseQuestionIdentity.objects.filter(question_id=question_id).first()
            if existing is not None:
                return existing
        except OperationalError as exc:
            if "locked" not in str(exc).casefold():
                raise
            last_error = exc

        if attempt < MAX_ALLOCATION_RETRIES - 1:
            sleep(0.01 * (attempt + 1))

    raise last_error
