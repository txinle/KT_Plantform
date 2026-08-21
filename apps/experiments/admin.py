from django.contrib import admin
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion, QuestionSnapshot
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
