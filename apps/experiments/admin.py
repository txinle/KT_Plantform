from django.contrib import admin
from .models import (
    ClassExperimentTask,
    Experiment,
    ExperimentQuestion,
    ExperimentStudent,
    ExperimentVersion,
    QuestionSnapshot,
)
Experiment._meta.verbose_name = "实验"
Experiment._meta.verbose_name_plural = "实验"
ExperimentQuestion._meta.verbose_name = "实验题目"
ExperimentQuestion._meta.verbose_name_plural = "实验题目"
ExperimentStudent._meta.verbose_name = "实验学生"
ExperimentStudent._meta.verbose_name_plural = "实验学生"
ExperimentVersion._meta.verbose_name = "实验发布版本"
ExperimentVersion._meta.verbose_name_plural = "实验发布版本"
QuestionSnapshot._meta.verbose_name = "题目快照"
QuestionSnapshot._meta.verbose_name_plural = "题目快照"
class QuestionInline(admin.TabularInline): model = ExperimentQuestion; extra = 0
class StudentInline(admin.TabularInline): model = ExperimentStudent; extra = 0
@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "created_by", "status", "ordering_mode", "feedback_mode", "time_mode")
    list_filter = ("course", "status")
    inlines = [QuestionInline, StudentInline]
admin.site.register(ExperimentVersion)
admin.site.register(QuestionSnapshot)


@admin.register(ClassExperimentTask)
class ClassExperimentTaskAdmin(admin.ModelAdmin):
    list_display = ("class_group", "experiment", "published_at")
    readonly_fields = (
        "class_group",
        "experiment",
        "release_snapshot",
        "class_snapshot",
        "published_at",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
