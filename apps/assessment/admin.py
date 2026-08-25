from django.contrib import admin
from .models import (
    AssessmentEvent,
    Attempt,
    ClientTimeSync,
    ConfidenceAssignment,
    DraftSaveEvent,
    EventSchemaArtifact,
    ExperimentSession,
    QuestionVisitEvent,
    ResponseSnapshot,
    SimulationSnapshot,
    StudentQuestionAssignment,
    SubmissionEvent,
)
ExperimentSession._meta.verbose_name = "实验会话"
ExperimentSession._meta.verbose_name_plural = "实验会话"
StudentQuestionAssignment._meta.verbose_name = "学生题目分配"
StudentQuestionAssignment._meta.verbose_name_plural = "学生题目分配"
Attempt._meta.verbose_name = "作答记录"
Attempt._meta.verbose_name_plural = "作答记录"
QuestionVisitEvent._meta.verbose_name = "题目访问事件"
QuestionVisitEvent._meta.verbose_name_plural = "题目访问事件"
DraftSaveEvent._meta.verbose_name = "草稿保存事件"
DraftSaveEvent._meta.verbose_name_plural = "草稿保存事件"
SubmissionEvent._meta.verbose_name = "提交事件"
SubmissionEvent._meta.verbose_name_plural = "提交事件"
admin.site.register(ExperimentSession)
admin.site.register(StudentQuestionAssignment)
admin.site.register(Attempt)
admin.site.register(QuestionVisitEvent)
admin.site.register(DraftSaveEvent)
admin.site.register(SubmissionEvent)


class ReadOnlyProcessDataAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(AssessmentEvent)
class AssessmentEventAdmin(ReadOnlyProcessDataAdmin):
    list_display = (
        "event_id",
        "event_type",
        "event_version",
        "session",
        "server_ingest_sequence_no",
        "server_received_at",
        "capture_mode",
    )
    list_filter = ("event_domain", "event_type", "event_version", "capture_mode")
    search_fields = ("event_id", "session__id", "correlation_id")


admin.site.register(ClientTimeSync, ReadOnlyProcessDataAdmin)
admin.site.register(ResponseSnapshot, ReadOnlyProcessDataAdmin)
admin.site.register(SimulationSnapshot, ReadOnlyProcessDataAdmin)
admin.site.register(ConfidenceAssignment, ReadOnlyProcessDataAdmin)
admin.site.register(EventSchemaArtifact, ReadOnlyProcessDataAdmin)
