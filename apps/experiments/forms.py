from django import forms
from apps.accounts.models import User
from .models import Experiment

class ExperimentForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ["name", "description", "question_bank", "ordering_mode", "feedback_mode", "time_mode", "total_time_limit_minutes", "per_question_time_limit_seconds", "resume_mode"]
        labels = {
            "name": "实验名称",
            "description": "实验说明",
            "question_bank": "题库",
            "ordering_mode": "题目顺序",
            "feedback_mode": "反馈模式",
            "time_mode": "时间模式",
            "total_time_limit_minutes": "实验总时限（分钟）",
            "per_question_time_limit_seconds": "单题时限（秒）",
            "resume_mode": "续答规则",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, owner=None, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["time_mode"].widget.attrs["data-time-mode-controller"] = ""
        if course is not None:
            self.fields["question_bank"].queryset = course.question_banks.exclude(visibility="archived")
        elif owner is not None:
            if owner.is_superuser or owner.role == User.Role.ADMIN:
                from apps.question_bank.models import QuestionBank
                self.fields["question_bank"].queryset = QuestionBank.objects.exclude(visibility=QuestionBank.Visibility.ARCHIVED)
            else:
                self.fields["question_bank"].queryset = owner.question_banks.exclude(visibility="archived")
    def clean(self):
        cleaned = super().clean()
        if cleaned.get("time_mode") == Experiment.TimeMode.TOTAL and not cleaned.get("total_time_limit_minutes"):
            self.add_error("total_time_limit_minutes", "总时限模式必须填写分钟数")
        if cleaned.get("time_mode") == Experiment.TimeMode.PER_QUESTION and not cleaned.get("per_question_time_limit_seconds"):
            self.add_error("per_question_time_limit_seconds", "单题时限模式必须填写秒数")
        return cleaned

class StudentAssignmentForm(forms.Form):
    students = forms.ModelMultipleChoiceField(queryset=User.objects.none(), widget=forms.CheckboxSelectMultiple, label="参加学生")
    def __init__(self, *args, owner=None, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = User.objects.filter(role=User.Role.STUDENT, is_active=True).select_related("class_group")
        if course is not None:
            qs = qs.filter(
                course_enrollments__course=course,
                course_enrollments__is_active=True,
                course_enrollments__class_group__is_active=True,
            )
        elif owner is not None and not (owner.is_superuser or owner.role == User.Role.ADMIN):
            qs = qs.filter(class_group__owner=owner)
        self.fields["students"].queryset = qs
