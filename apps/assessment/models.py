from django.conf import settings
from django.db import models
from django.db.models import Q

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
