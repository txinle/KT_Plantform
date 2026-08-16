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

@admin.register(QuestionBank)
class QuestionBankAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "created_by", "visibility", "updated_at")
    list_filter = ("course", "visibility")
    search_fields = ("name", "course__name", "course__code")


@admin.register(Concept)
class ConceptAdmin(admin.ModelAdmin):
    list_display = ("name", "course", "scope", "owner")
    list_filter = ("course", "scope")
    search_fields = ("name", "normalized_name", "course__name")
