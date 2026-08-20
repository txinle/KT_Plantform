from django import forms
from apps.accounts.models import User
from apps.question_bank.models import Question
from .models import Experiment

class ExperimentForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = ["name", "description", "question_bank", "selection_mode", "ordering_mode", "feedback_mode", "time_mode", "total_time_limit_minutes", "per_question_time_limit_seconds", "resume_mode"]
        labels = {
            "name": "实验名称",
            "description": "实验说明",
            "question_bank": "题库",
            "selection_mode": "选题方式",
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
        self.fields["selection_mode"].widget.attrs["data-selection-mode-controller"] = ""
        self.fields["selection_mode"].required = False
        quota_values = {}
        if self.instance.pk:
            quota_values = dict(
                self.instance.question_quotas.values_list("question_type", "amount")
            )
        for question_type, label in Question.Type.choices:
            field_name = f"quota_{question_type}"
            self.fields[field_name] = forms.IntegerField(
                label=f"{label}数量",
                min_value=0,
                required=False,
                initial=quota_values.get(question_type, 0),
                widget=forms.NumberInput(
                    attrs={
                        "min": 0,
                        "step": 1,
                        "data-quota-input": question_type,
                    }
                ),
            )
        self.quota_total = sum(
            self._submitted_quota_amount(question_type, quota_values.get(question_type, 0))
            for question_type in Question.Type.values
        )
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
        if not cleaned.get("selection_mode"):
            cleaned["selection_mode"] = Experiment.SelectionMode.ALL
        if cleaned.get("time_mode") == Experiment.TimeMode.TOTAL and not cleaned.get("total_time_limit_minutes"):
            self.add_error("total_time_limit_minutes", "总时限模式必须填写分钟数")
        if cleaned.get("time_mode") == Experiment.TimeMode.PER_QUESTION and not cleaned.get("per_question_time_limit_seconds"):
            self.add_error("per_question_time_limit_seconds", "单题时限模式必须填写秒数")
        self.quota_amounts = {
            question_type: cleaned.get(f"quota_{question_type}") or 0
            for question_type in Question.Type.values
        }
        if (
            cleaned.get("selection_mode") == Experiment.SelectionMode.QUOTA
            and sum(self.quota_amounts.values()) <= 0
        ):
            self.add_error("selection_mode", "按题型配额抽题至少需要抽取 1 道题")
        return cleaned

    def _submitted_quota_amount(self, question_type, fallback):
        raw_value = self.data.get(f"quota_{question_type}") if self.is_bound else fallback
        try:
            return max(0, int(raw_value or 0))
        except (TypeError, ValueError):
            return 0

    def save_quotas(self, experiment):
        for question_type, amount in self.quota_amounts.items():
            experiment.question_quotas.update_or_create(
                question_type=question_type,
                defaults={"amount": amount},
            )

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
        else:
            qs = qs.exclude(class_group__is_active=False)
            if owner is not None and not (owner.is_superuser or owner.role == User.Role.ADMIN):
                qs = qs.filter(class_group__owner=owner)
        self.fields["students"].queryset = qs
