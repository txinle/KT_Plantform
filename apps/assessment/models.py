import hashlib
import uuid
from contextlib import contextmanager
from contextvars import ContextVar

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models import Q
from django.utils import timezone


HASH_VALIDATOR = RegexValidator(
    regex=r"^[0-9a-f]{64}$",
    message="内容哈希必须是 64 位小写十六进制 SHA-256。",
)

ALLOWED_QUALITY_FLAGS = frozenset(
    {
        "clock_unsynchronized",
        "offline_buffered",
        "mid_session_capture",
        "late_arrival",
        "payload_recovered",
    }
)


def validate_quality_flags(value):
    if not isinstance(value, list) or any(
        not isinstance(flag, str) or flag not in ALLOWED_QUALITY_FLAGS
        for flag in value
    ):
        raise ValidationError("事件质量标记包含未定义值。")
    if len(value) != len(set(value)):
        raise ValidationError("事件质量标记不能重复。")


class AppendOnlyEventError(RuntimeError):
    pass


_EVENT_APPEND_ALLOWED = ContextVar("assessment_event_append_allowed", default=False)
_EVENT_SCHEMA_ARTIFACT_APPEND_ALLOWED = ContextVar(
    "event_schema_artifact_append_allowed",
    default=False,
)
_SIMULATION_SNAPSHOT_APPEND_ALLOWED = ContextVar(
    "simulation_snapshot_append_allowed",
    default=False,
)


@contextmanager
def _assessment_event_append_scope():
    token = _EVENT_APPEND_ALLOWED.set(True)
    try:
        yield
    finally:
        _EVENT_APPEND_ALLOWED.reset(token)


@contextmanager
def _event_schema_artifact_append_scope():
    token = _EVENT_SCHEMA_ARTIFACT_APPEND_ALLOWED.set(True)
    try:
        yield
    finally:
        _EVENT_SCHEMA_ARTIFACT_APPEND_ALLOWED.reset(token)


@contextmanager
def _simulation_snapshot_append_scope():
    token = _SIMULATION_SNAPSHOT_APPEND_ALLOWED.set(True)
    try:
        yield
    finally:
        _SIMULATION_SNAPSHOT_APPEND_ALLOWED.reset(token)


class AssessmentEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")

    def delete(self):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")

    def bulk_create(self, objs, **kwargs):
        if not _EVENT_APPEND_ALLOWED.get():
            raise AppendOnlyEventError(
                "AssessmentEvent rows must use append_assessment_event()."
            )
        return super().bulk_create(objs, **kwargs)


class AssessmentEventManager(models.Manager.from_queryset(AssessmentEventQuerySet)):
    pass


class ImmutableArtifactQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyEventError("Event schema artifacts are immutable.")

    def delete(self):
        raise AppendOnlyEventError("Event schema artifacts are immutable.")

    def bulk_create(self, objs, **kwargs):
        raise AppendOnlyEventError(
            "Immutable process records must use their validated create service."
        )


class ImmutableArtifactManager(models.Manager.from_queryset(ImmutableArtifactQuerySet)):
    pass

class ExperimentSession(models.Model):
    class Status(models.TextChoices):
        IN_PROGRESS = "in_progress", "进行中"
        COMPLETED = "completed", "已完成"
        EXPIRED = "expired", "已超时"
        INVALIDATED = "invalidated", "已作废"
    version = models.ForeignKey("experiments.ExperimentVersion", on_delete=models.PROTECT, related_name="sessions")
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="assessment_sessions")
    class_snapshot_name = models.CharField(max_length=160, blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.IN_PROGRESS)
    started_at = models.DateTimeField(auto_now_add=True)
    last_activity_at = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    deadline_at = models.DateTimeField(null=True, blank=True)
    current_assignment = models.ForeignKey("StudentQuestionAssignment", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    submission_count = models.PositiveIntegerField(default=0)
    first_open_count = models.PositiveIntegerField(default=0)
    visit_event_count = models.PositiveIntegerField(default=0)
    sampling_seed = models.CharField(max_length=64, blank=True, default="")
    ordering_seed = models.CharField(max_length=64, blank=True, default="")
    sampling_algorithm_version = models.CharField(max_length=32, blank=True, default="")
    assignment_unit_type = models.CharField(max_length=16, choices=(("subject", "Subject"), ("session", "Session")), default="subject")
    condition = models.ForeignKey("experiments.Condition", null=True, blank=True, on_delete=models.PROTECT, related_name="sessions")
    condition_assigned_at = models.DateTimeField(null=True, blank=True)
    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "student"], name="unique_student_version_session")]

    def save(self, *args, **kwargs):
        if self.pk and self.condition_id != type(self).objects.only("condition_id").get(pk=self.pk).condition_id:
            raise ValidationError("session condition is immutable")
        return super().save(*args, **kwargs)

class StudentQuestionAssignment(models.Model):
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="assignments")
    snapshot = models.ForeignKey("experiments.QuestionSnapshot", on_delete=models.PROTECT)
    item_instance = models.ForeignKey(
        "experiments.ItemInstance",
        on_delete=models.PROTECT,
        related_name="assignments",
    )
    assigned_sequence_index = models.PositiveIntegerField()
    random_seed = models.CharField(max_length=64, blank=True)
    class Meta:
        ordering = ["assigned_sequence_index"]
        constraints = [
            models.UniqueConstraint(fields=["session", "snapshot"], name="unique_session_snapshot"),
            models.UniqueConstraint(fields=["session", "assigned_sequence_index"], name="unique_session_assigned_sequence"),
        ]

class Attempt(models.Model):
    class Status(models.TextChoices):
        UNVISITED = "unvisited", "未访问"
        VISITED = "visited", "已访问"
        DRAFT = "draft", "已有草稿"
        SKIPPED = "skipped", "已跳过"
        SUBMITTED = "submitted", "已提交"
        TIMED_OUT = "timed_out", "已超时"
        UNANSWERED = "unanswered", "未作答"
    assignment = models.OneToOneField(StudentQuestionAssignment, on_delete=models.CASCADE, related_name="attempt")
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="attempts")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.UNVISITED)
    first_open_sequence_index = models.PositiveIntegerField(null=True, blank=True)
    submission_sequence_index = models.PositiveIntegerField(null=True, blank=True)
    first_opened_at = models.DateTimeField(null=True, blank=True)
    last_opened_at = models.DateTimeField(null=True, blank=True)
    visit_count = models.PositiveIntegerField(default=0)
    active_seconds = models.PositiveIntegerField(default=0)
    draft_answer = models.JSONField(null=True, blank=True)
    draft_saved_at = models.DateTimeField(null=True, blank=True)
    # Durable pointers to immutable response snapshots.  These are the sole
    # source of truth for text responses across requests; draft_answer/final_answer
    # remain compatibility projections for existing callers.
    current_response_snapshot = models.ForeignKey(
        "ResponseSnapshot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="current_for_attempts",
    )
    final_answer = models.JSONField(null=True, blank=True)
    final_response_snapshot = models.ForeignKey(
        "ResponseSnapshot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="final_for_attempts",
    )
    submitted_at = models.DateTimeField(null=True, blank=True)
    first_to_submit_duration_ms = models.BigIntegerField(null=True, blank=True)
    last_visit_to_submission_ms = models.BigIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    submission_token = models.UUIDField(null=True, blank=True, unique=True)
    writer_lease_token = models.UUIDField(null=True, blank=True, editable=False)
    writer_client_instance_id = models.UUIDField(null=True, blank=True, editable=False)
    writer_lease_expires_at = models.DateTimeField(null=True, blank=True, editable=False)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "submission_sequence_index"],
                condition=Q(submission_sequence_index__isnull=False),
                name="unique_session_submission_sequence",
            )
        ]

    @property
    def saved_response_snapshot(self):
        """Backward-compatible name for the durable current snapshot pointer."""
        return self.current_response_snapshot

    @property
    def submission(self):
        """Canonical relationship name retained alongside submission_event."""
        return self.submission_event

    @property
    def saved_response_state(self):
        """Compatibility projection for choice drafts, derived from draft_answer."""
        if self.draft_answer is None:
            return None
        question_type = self.assignment.snapshot.question_type
        if question_type in {"single_choice", "multiple_choice", "true_false"}:
            # Import lazily to avoid model/service import cycles.
            from .response_snapshot_services import canonical_choice_response_state

            return canonical_choice_response_state(question_type, self.draft_answer)
        return None

class QuestionVisitEvent(models.Model):
    session = models.ForeignKey(ExperimentSession, on_delete=models.CASCADE, related_name="visit_events")
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="visit_events")
    from_assignment = models.ForeignKey(StudentQuestionAssignment, null=True, blank=True, on_delete=models.SET_NULL, related_name="visits_from")
    visit_index = models.PositiveIntegerField()
    event_sequence = models.PositiveIntegerField()
    entry_reason = models.CharField(max_length=32, default="navigation_click")
    client_occurred_at = models.DateTimeField(null=True, blank=True)
    server_received_at = models.DateTimeField(auto_now_add=True)

class DraftSaveEvent(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.CASCADE, related_name="draft_events")
    answer_hash = models.CharField(max_length=64)
    client_occurred_at = models.DateTimeField(null=True, blank=True)
    server_received_at = models.DateTimeField(auto_now_add=True)

class SubmissionEvent(models.Model):
    class GradingStatus(models.TextChoices):
        PENDING = "pending", "等待评分"
        COMPLETED = "completed", "评分完成"
        REGRADE = "regrade", "待重新评分"

    attempt = models.OneToOneField(Attempt, on_delete=models.CASCADE, related_name="submission_event")
    submission_token = models.UUIDField(unique=True)
    final_answer = models.JSONField(null=True, blank=True)
    final_response_snapshot = models.ForeignKey(
        "ResponseSnapshot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="submissions",
    )
    grading_status = models.CharField(
        max_length=16,
        choices=GradingStatus.choices,
        default=GradingStatus.COMPLETED,
    )
    # Compatibility projections. Canonical grade values live in GradeRecord.
    is_correct = models.BooleanField(null=True, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    client_clicked_at = models.DateTimeField(null=True, blank=True)
    server_received_at = models.DateTimeField()
    committed_at = models.DateTimeField()

    def clean(self):
        super().clean()
        if (
            self.final_response_snapshot_id
            and self.attempt_id
            and self.final_response_snapshot
            and self.final_response_snapshot.attempt_id != self.attempt_id
        ):
            raise ValidationError(
                {"final_response_snapshot": "最终响应快照必须属于同一作答。"}
            )


# Canonical domain name; SubmissionEvent remains an import-compatible alias.
Submission = SubmissionEvent


class GradeRecord(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "等待评分"
        COMPLETED = "completed", "评分完成"
        REGRADE = "regrade", "待重新评分"

    submission = models.ForeignKey(
        SubmissionEvent,
        on_delete=models.CASCADE,
        related_name="grade_records",
    )
    version = models.PositiveIntegerField(default=1)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    is_correct = models.BooleanField(null=True, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    max_score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    normalized_response = models.JSONField(null=True, blank=True)
    grading_method = models.CharField(max_length=32, default="automatic")
    grading_version = models.CharField(max_length=64, blank=True, default="")
    grader_version = models.CharField(max_length=64, blank=True, default="")
    graded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    supersedes_grade = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="superseding_grades",
    )

    class Meta:
        ordering = ["submission_id", "-version"]
        constraints = [
            models.UniqueConstraint(
                fields=["submission", "version"],
                name="unique_submission_grade_version",
            ),
            models.CheckConstraint(
                condition=Q(version__gte=1),
                name="grade_record_version_positive",
            ),
        ]

    @property
    def grading_status(self):
        return self.status

    @grading_status.setter
    def grading_status(self, value):
        self.status = value


class EventSchemaArtifact(models.Model):
    event_type = models.CharField(max_length=64)
    event_version = models.PositiveSmallIntegerField()
    schema_sha256 = models.CharField(max_length=64, validators=[HASH_VALIDATOR])
    schema_document = models.JSONField()
    registered_at = models.DateTimeField(auto_now_add=True)

    objects = ImmutableArtifactManager()

    class Meta:
        ordering = ["event_type", "event_version"]
        constraints = [
            models.UniqueConstraint(
                fields=["event_type", "event_version"],
                name="unique_event_schema_artifact",
            ),
            models.CheckConstraint(
                condition=Q(event_version__gte=1),
                name="event_schema_version_positive",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not _EVENT_SCHEMA_ARTIFACT_APPEND_ALLOWED.get():
            raise AppendOnlyEventError(
                "Event schema artifacts must be registered by the append service."
            )
        if not self._state.adding:
            raise AppendOnlyEventError("Event schema artifacts are immutable.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyEventError("Event schema artifacts are immutable.")


class ClientTimeSync(models.Model):
    class SyncQuality(models.TextChoices):
        PENDING = "pending", "等待客户端完成往返观测"
        VALID = "valid", "有效"
        INVALID = "invalid", "无效"
        CLOCK_CHANGED = "clock_changed", "客户端时钟突变"

    time_sync_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    session = models.ForeignKey(
        ExperimentSession,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="client_time_syncs",
    )
    client_instance_id = models.UUIDField()
    client_wall_time_utc = models.DateTimeField()
    client_monotonic_ms = models.DecimalField(max_digits=20, decimal_places=3)
    client_time_origin = models.DateTimeField()
    server_received_at = models.DateTimeField()
    server_responded_at = models.DateTimeField()
    client_received_wall_time_utc = models.DateTimeField(null=True, blank=True)
    client_received_monotonic_ms = models.DecimalField(
        max_digits=20,
        decimal_places=3,
        null=True,
        blank=True,
    )
    estimated_clock_offset_ms = models.BigIntegerField(null=True, blank=True)
    estimated_rtt_ms = models.PositiveBigIntegerField(null=True, blank=True)
    sync_quality = models.CharField(
        max_length=20,
        choices=SyncQuality.choices,
        default=SyncQuality.PENDING,
    )
    finalized_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["client_instance_id", "created_at", "time_sync_id"]
        indexes = [
            models.Index(
                fields=["client_instance_id", "created_at"],
                name="assess_sync_client_created_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(server_responded_at__gte=models.F("server_received_at")),
                name="client_sync_response_after_receive",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        sync_quality="pending",
                        client_received_wall_time_utc__isnull=True,
                        client_received_monotonic_ms__isnull=True,
                        estimated_clock_offset_ms__isnull=True,
                        estimated_rtt_ms__isnull=True,
                        finalized_at__isnull=True,
                    )
                    | Q(
                        sync_quality="valid",
                        client_received_wall_time_utc__isnull=False,
                        client_received_monotonic_ms__isnull=False,
                        estimated_clock_offset_ms__isnull=False,
                        estimated_rtt_ms__isnull=False,
                        finalized_at__isnull=False,
                    )
                    | Q(
                        sync_quality__in=("invalid", "clock_changed"),
                        client_received_wall_time_utc__isnull=False,
                        client_received_monotonic_ms__isnull=False,
                        estimated_clock_offset_ms__isnull=True,
                        estimated_rtt_ms__isnull=True,
                        finalized_at__isnull=False,
                    )
                ),
                name="client_sync_state_complete",
            ),
        ]


class ResponseSnapshot(models.Model):
    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        Attempt,
        on_delete=models.PROTECT,
        related_name="response_snapshots",
    )
    content = models.TextField(blank=True)
    content_hash = models.CharField(max_length=64, validators=[HASH_VALIDATOR])
    content_length = models.PositiveIntegerField()
    # Canonical logical state, independent from snapshot_id.  ``content`` and
    # ``content_hash`` remain for backwards-compatible consumers.
    state_json = models.JSONField(null=True, blank=True)
    state_hash = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        validators=[HASH_VALIDATOR],
    )
    created_at = models.DateTimeField(auto_now_add=True)
    schema_version = models.PositiveSmallIntegerField(default=1)

    objects = ImmutableArtifactManager()

    class Meta:
        ordering = ["attempt_id", "created_at", "snapshot_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "content_hash"],
                name="unique_attempt_response_hash",
            ),
            models.CheckConstraint(
                condition=Q(schema_version__gte=1),
                name="response_snapshot_schema_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if self.state_json is None:
            question_type = "short_answer"
            try:
                question_type = self.attempt.assignment.snapshot.question_type
            except Exception:
                pass
            self.state_json = {"question_type": question_type, "value": self.content}
        if self.state_hash is None:
            import json

            canonical = json.dumps(
                self.state_json,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            self.state_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        expected_hash = hashlib.sha256(self.content.encode("utf-8")).hexdigest()
        errors = {}
        if self.content_hash != expected_hash:
            errors["content_hash"] = "响应哈希与 UTF-8 正文不一致。"
        if self.content_length != len(self.content):
            errors["content_length"] = "响应长度与正文字符数不一致。"
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise AppendOnlyEventError("ResponseSnapshot rows are immutable.")
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyEventError("ResponseSnapshot rows are immutable.")


class SimulationSnapshot(models.Model):
    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        Attempt,
        on_delete=models.PROTECT,
        related_name="simulation_snapshots",
    )
    simulation_version = models.CharField(max_length=64)
    state_metadata = models.JSONField(default=dict)
    blob_storage_key = models.CharField(max_length=512)
    content_hash = models.CharField(max_length=64, validators=[HASH_VALIDATOR])
    content_size = models.PositiveBigIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    schema_version = models.PositiveSmallIntegerField(default=1)

    objects = ImmutableArtifactManager()

    class Meta:
        ordering = ["attempt_id", "created_at", "snapshot_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["attempt", "content_hash"],
                name="unique_attempt_simulation_hash",
            ),
            models.CheckConstraint(
                condition=Q(schema_version__gte=1),
                name="simulation_snapshot_schema_positive",
            ),
        ]

    def clean(self):
        super().clean()
        if not isinstance(self.state_metadata, dict):
            raise ValidationError(
                {"state_metadata": "仿真快照元数据必须是 JSON 对象。"}
            )

    def save(self, *args, **kwargs):
        if self._state.adding and not _SIMULATION_SNAPSHOT_APPEND_ALLOWED.get():
            raise AppendOnlyEventError(
                "SimulationSnapshot rows must use create_simulation_snapshot()."
            )
        if not self._state.adding:
            raise AppendOnlyEventError("SimulationSnapshot rows are immutable.")
        self.full_clean(validate_unique=False, validate_constraints=False)
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyEventError("SimulationSnapshot rows are immutable.")


class ConfidenceAssignment(models.Model):
    class Condition(models.TextChoices):
        NOT_ASSIGNED = "not_assigned", "未分配"
        CONTROL = "control", "对照"
        PROMPT = "prompt", "采集信心"

    class AssignmentUnit(models.TextChoices):
        STUDENT = "student", "学生"
        SESSION = "session", "会话"
        ATTEMPT = "attempt", "作答"

    class ResponseStatus(models.TextChoices):
        NOT_ASSIGNED = "not_assigned", "未分配"
        ASSIGNED = "assigned", "已分配未显示"
        PROMPT_SHOWN = "prompt_shown", "已显示未响应"
        COMMITTED = "committed", "已提交"
        SKIPPED = "skipped", "已跳过"
        UPLOAD_FAILED = "upload_failed", "上传失败"

    confidence_assignment_id = models.UUIDField(
        primary_key=True,
        default=uuid.uuid4,
        editable=False,
    )
    attempt = models.OneToOneField(
        Attempt,
        on_delete=models.PROTECT,
        related_name="confidence_assignment",
    )
    condition = models.CharField(max_length=16, choices=Condition.choices)
    assigned_at = models.DateTimeField(default=timezone.now)
    assignment_unit = models.CharField(max_length=12, choices=AssignmentUnit.choices)
    assignment_version = models.CharField(max_length=64)
    prompt_shown_at = models.DateTimeField(null=True, blank=True)
    response_status = models.CharField(max_length=16, choices=ResponseStatus.choices)
    probability = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        validators=[MinValueValidator(0), MaxValueValidator(100)],
    )
    committed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["attempt__session_id", "attempt_id"]
        constraints = [
            models.CheckConstraint(
                condition=Q(probability__isnull=True)
                | Q(probability__gte=0, probability__lte=100),
                name="confidence_probability_percent_range",
            ),
            models.CheckConstraint(
                condition=(
                    Q(
                        response_status="committed",
                        probability__isnull=False,
                        committed_at__isnull=False,
                    )
                    | (
                        ~Q(response_status="committed")
                        & Q(probability__isnull=True, committed_at__isnull=True)
                    )
                ),
                name="confidence_commit_fields_match_status",
            ),
            models.CheckConstraint(
                condition=(
                    Q(condition="not_assigned", response_status="not_assigned")
                    | (
                        ~Q(condition="not_assigned")
                        & ~Q(response_status="not_assigned")
                    )
                ),
                name="confidence_assignment_status_consistent",
            ),
        ]


class AssessmentEvent(models.Model):
    class EventDomain(models.TextChoices):
        ASSESSMENT = "assessment", "作答"
        TELEMETRY = "telemetry", "遥测"
        SYSTEM = "system", "系统"

    class ActorSource(models.TextChoices):
        CLIENT = "client", "客户端"
        SERVER = "server", "服务端"
        SYSTEM = "system", "系统"

    class CaptureMode(models.TextChoices):
        NATIVE = "native", "原生采集"
        LEGACY_PROJECTION = "legacy_projection", "历史投影"

    event_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    event_domain = models.CharField(max_length=16, choices=EventDomain.choices)
    event_type = models.CharField(max_length=64)
    event_version = models.PositiveSmallIntegerField(default=1)
    envelope_version = models.PositiveSmallIntegerField(default=1)
    actor_source = models.CharField(max_length=12, choices=ActorSource.choices)

    session = models.ForeignKey(
        ExperimentSession,
        on_delete=models.PROTECT,
        related_name="assessment_events",
    )
    assignment = models.ForeignKey(
        StudentQuestionAssignment,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assessment_events",
    )
    attempt = models.ForeignKey(
        Attempt,
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assessment_events",
    )
    question_snapshot = models.ForeignKey(
        "experiments.QuestionSnapshot",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assessment_events",
    )
    item_instance = models.ForeignKey(
        "experiments.ItemInstance",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="assessment_events",
    )

    client_instance_id = models.UUIDField(null=True, blank=True)
    client_sequence_no = models.PositiveBigIntegerField(null=True, blank=True)
    server_ingest_sequence_no = models.PositiveBigIntegerField()
    correlation_id = models.UUIDField(null=True, blank=True)

    client_wall_time_utc = models.DateTimeField(null=True, blank=True)
    client_monotonic_ms = models.DecimalField(
        max_digits=20,
        decimal_places=3,
        null=True,
        blank=True,
    )
    client_time_origin = models.DateTimeField(null=True, blank=True)
    server_received_at = models.DateTimeField(default=timezone.now)
    server_committed_at = models.DateTimeField(default=timezone.now)

    time_sync = models.ForeignKey(
        ClientTimeSync,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="events",
    )
    estimated_clock_offset_ms = models.BigIntegerField(null=True, blank=True)
    estimated_rtt_ms = models.PositiveBigIntegerField(null=True, blank=True)
    estimated_occurred_at_utc = models.DateTimeField(null=True, blank=True)
    class IntervalQuality(models.TextChoices):
        EXACT_MONOTONIC = "exact_monotonic", "同一客户端单调时钟"
        SYNCHRONIZED_ESTIMATE = "synchronized_estimate", "同步估计"
        DEGRADED = "degraded", "降级估计"
        UNKNOWN = "unknown", "未知"

    interval_quality = models.CharField(
        max_length=24,
        choices=IntervalQuality.choices,
        default=IntervalQuality.UNKNOWN,
    )

    capture_mode = models.CharField(
        max_length=24,
        choices=CaptureMode.choices,
        default=CaptureMode.NATIVE,
    )
    quality_flags = models.JSONField(
        default=list,
        blank=True,
        validators=[validate_quality_flags],
    )
    payload = models.JSONField(default=dict, blank=True)
    app_version = models.CharField(max_length=64, blank=True)

    objects = AssessmentEventManager()

    class Meta:
        ordering = ["session_id", "server_ingest_sequence_no"]
        constraints = [
            models.UniqueConstraint(
                fields=["session", "server_ingest_sequence_no"],
                name="unique_session_ingest_sequence",
            ),
            models.UniqueConstraint(
                fields=["client_instance_id", "client_sequence_no"],
                condition=Q(
                    client_instance_id__isnull=False,
                    client_sequence_no__isnull=False,
                ),
                name="unique_client_event_sequence",
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=Q(
                    attempt__isnull=False,
                    event_type="response_committed",
                ),
                name="unique_attempt_response_commit",
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=Q(
                    attempt__isnull=False,
                    event_type="confidence_prompt_shown",
                ),
                name="unique_attempt_confidence_prompt",
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=Q(
                    attempt__isnull=False,
                    event_type__in=("confidence_committed", "confidence_skipped"),
                ),
                name="unique_attempt_confidence_result",
            ),
            models.UniqueConstraint(
                fields=["session"],
                condition=Q(event_type="session_started"),
                name="unique_session_started_event",
            ),
            models.UniqueConstraint(
                fields=["session"],
                condition=Q(
                    event_type__in=(
                        "session_completed",
                        "session_expired",
                        "session_invalidated",
                    )
                ),
                name="unique_session_terminal_event",
            ),
            models.UniqueConstraint(
                fields=["attempt"],
                condition=Q(attempt__isnull=False, event_type="attempt_closed"),
                name="unique_attempt_closed_event",
            ),
            models.UniqueConstraint(
                fields=["attempt", "correlation_id"],
                condition=Q(
                    attempt__isnull=False,
                    correlation_id__isnull=False,
                    event_type="response_commit_rejected",
                ),
                name="unique_attempt_rejection_correlation",
            ),
            models.CheckConstraint(
                condition=(
                    Q(client_instance_id__isnull=True, client_sequence_no__isnull=True)
                    | Q(client_instance_id__isnull=False, client_sequence_no__isnull=False)
                ),
                name="event_client_sequence_pair_complete",
            ),
            models.CheckConstraint(
                condition=Q(event_version__gte=1, envelope_version__gte=1),
                name="event_versions_positive",
            ),
            models.CheckConstraint(
                condition=Q(server_ingest_sequence_no__gte=1),
                name="event_ingest_sequence_positive",
            ),
            models.CheckConstraint(
                condition=Q(client_sequence_no__isnull=True)
                | Q(client_sequence_no__gte=1),
                name="event_client_sequence_positive",
            ),
            models.CheckConstraint(
                condition=Q(server_committed_at__gte=models.F("server_received_at")),
                name="event_commit_after_receive",
            ),
        ]
        indexes = [
            models.Index(
                fields=[
                    "attempt",
                    "client_instance_id",
                    "client_sequence_no",
                ],
                name="assess_evt_attempt_client_idx",
            ),
            models.Index(
                fields=["event_type", "server_received_at"],
                name="assess_event_type_received_idx",
            ),
        ]

    def save(self, *args, **kwargs):
        if self._state.adding and not _EVENT_APPEND_ALLOWED.get():
            raise AppendOnlyEventError(
                "AssessmentEvent rows must use append_assessment_event()."
            )
        if not self._state.adding:
            raise AppendOnlyEventError("AssessmentEvent rows are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")

    @property
    def version(self):
        return self.event_version

    @property
    def domain(self):
        return self.event_domain

    @property
    def actor(self):
        return self.actor_source

    @property
    def sequence(self):
        return self.client_sequence_no
