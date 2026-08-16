from django.contrib import admin
from .models import Experiment, ExperimentQuestion, ExperimentStudent, ExperimentVersion, QuestionSnapshot
class QuestionInline(admin.TabularInline): model = ExperimentQuestion; extra = 0
class StudentInline(admin.TabularInline): model = ExperimentStudent; extra = 0
@admin.register(Experiment)
class ExperimentAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "created_by", "status", "ordering_mode", "feedback_mode", "time_mode")
    list_filter = ("course", "status")
    inlines = [QuestionInline, StudentInline]
admin.site.register(ExperimentVersion)
admin.site.register(QuestionSnapshot)
