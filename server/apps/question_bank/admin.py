from django.contrib import admin
from django import forms
from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
QuestionBank._meta.verbose_name = "题库"
QuestionBank._meta.verbose_name_plural = "题库"
Question._meta.verbose_name = "题目"
Question._meta.verbose_name_plural = "题目"
QuestionOption._meta.verbose_name = "题目选项"
QuestionOption._meta.verbose_name_plural = "题目选项"
Concept._meta.verbose_name = "知识点"
Concept._meta.verbose_name_plural = "知识点"
QuestionConcept._meta.verbose_name = "题目知识点映射"
QuestionConcept._meta.verbose_name_plural = "题目知识点映射"
class OptionInline(admin.TabularInline): model = QuestionOption; extra = 0
class ConceptInline(admin.TabularInline): model = QuestionConcept; extra = 0


class QuestionAdminForm(forms.ModelForm):
    class Meta:
        model = Question
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.is_bound:
            self.data = self.data.copy()
            for field in ('educational_type', 'max_score', 'content_language', 'text_match_policy_version'):
                if field not in self.data:
                    self.data[field] = getattr(self.instance, field)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    form = QuestionAdminForm
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
