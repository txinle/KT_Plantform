from django.contrib import admin
from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
class OptionInline(admin.TabularInline): model = QuestionOption; extra = 0
class ConceptInline(admin.TabularInline): model = QuestionConcept; extra = 0
@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("code", "bank", "sequence", "question_type", "teacher_difficulty", "status")
    list_filter = ("question_type", "status", "bank")
    search_fields = ("code", "text")
    inlines = [OptionInline, ConceptInline]
admin.site.register(QuestionBank)
admin.site.register(Concept)
