import uuid

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


class AssessmentEventQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")

    def delete(self):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")


class AssessmentEventManager(models.Manager.from_queryset(AssessmentEventQuerySet)):
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
    class Meta:
        constraints = [models.UniqueConstraint(fields=["version", "student"], name="unique_student_version_session")]

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
    final_answer = models.JSONField(null=True, blank=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    first_to_submit_duration_ms = models.BigIntegerField(null=True, blank=True)
    last_visit_to_submission_ms = models.BigIntegerField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)
    score = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)
    submission_token = models.UUIDField(null=True, blank=True, unique=True)
    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["session", "submission_sequence_index"],
                condition=Q(submission_sequence_index__isnull=False),
                name="unique_session_submission_sequence",
            )
        ]

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
    attempt = models.OneToOneField(Attempt, on_delete=models.CASCADE, related_name="submission_event")
    submission_token = models.UUIDField(unique=True)
    final_answer = models.JSONField(null=True, blank=True)
    is_correct = models.BooleanField()
    score = models.DecimalField(max_digits=5, decimal_places=2)
    client_clicked_at = models.DateTimeField(null=True, blank=True)
    server_received_at = models.DateTimeField()
    committed_at = models.DateTimeField()


class ClientTimeSync(models.Model):
    class SyncQuality(models.TextChoices):
        VALID = "valid", "有效"
        INVALID = "invalid", "无效"
        CLOCK_CHANGED = "clock_changed", "客户端时钟突变"

    time_sync_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    client_instance_id = models.UUIDField()
    client_wall_time_utc = models.DateTimeField()
    client_monotonic_ms = models.DecimalField(max_digits=20, decimal_places=3)
    client_time_origin = models.DateTimeField()
    server_received_at = models.DateTimeField()
    server_responded_at = models.DateTimeField()
    estimated_clock_offset_ms = models.BigIntegerField()
    estimated_rtt_ms = models.PositiveBigIntegerField()
    sync_quality = models.CharField(
        max_length=20,
        choices=SyncQuality.choices,
        default=SyncQuality.VALID,
    )
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
            )
        ]


class ResponseSnapshot(models.Model):
    snapshot_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        Attempt,
        on_delete=models.PROTECT,
        related_name="response_snapshots",
    )
    content = models.TextField()
    content_hash = models.CharField(max_length=64, validators=[HASH_VALIDATOR])
    content_length = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)
    schema_version = models.PositiveSmallIntegerField(default=1)

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

    capture_mode = models.CharField(
        max_length=24,
        choices=CaptureMode.choices,
        default=CaptureMode.NATIVE,
    )
    quality_flags = models.JSONField(default=list, validators=[validate_quality_flags])
    payload = models.JSONField(default=dict)
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
        if not self._state.adding:
            raise AppendOnlyEventError("AssessmentEvent rows are append-only.")
        return super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyEventError("AssessmentEvent rows are append-only.")
