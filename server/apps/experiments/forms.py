from django import forms
from django.core.exceptions import ValidationError
from apps.accounts.models import ClassGroup, User
from apps.question_bank.models import Question
from .models import Experiment

class ExperimentForm(forms.ModelForm):
    class Meta:
        model = Experiment
        fields = [
            "name",
            "description",
            "question_bank",
            "selection_mode",
            "ordering_mode",
            "feedback_mode",
            "feedback_release_policy",
            "time_mode",
            "total_time_limit_minutes",
            "per_question_time_limit_seconds",
            "resume_mode",
            "confidence_mode",
            "confidence_sampling_unit",
            "confidence_probability",
            "confidence_randomization_seed",
            "confidence_assignment_version",
            "confidence_scale_version",
            "confidence_prompt_version",
            "heartbeat_interval_seconds",
        ]
        labels = {
            "name": "实验名称",
            "description": "实验说明",
            "question_bank": "题库",
            "selection_mode": "选题方式",
            "ordering_mode": "题目顺序",
            "feedback_mode": "反馈模式",
            "feedback_release_policy": "反馈发布时间",
            "time_mode": "时间模式",
            "total_time_limit_minutes": "实验总时限（分钟）",
            "per_question_time_limit_seconds": "单题时限（秒）",
            "resume_mode": "续答规则",
            "confidence_mode": "作答信心采集",
            "confidence_sampling_unit": "信心抽样单位",
            "confidence_probability": "信心抽样概率",
            "confidence_randomization_seed": "随机分配种子（可选）",
            "confidence_assignment_version": "分配规则版本",
            "confidence_scale_version": "信心量表版本",
            "confidence_prompt_version": "信心提示版本",
            "heartbeat_interval_seconds": "活跃心跳间隔（秒）",
        }
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, owner=None, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in (
            "feedback_release_policy",
            "confidence_mode",
            "confidence_sampling_unit",
            "confidence_probability",
            "confidence_randomization_seed",
            "confidence_assignment_version",
            "confidence_scale_version",
            "confidence_prompt_version",
            "heartbeat_interval_seconds",
        ):
            self.fields[field_name].required = False
        self.fields["time_mode"].widget.attrs["data-time-mode-controller"] = ""
        self.fields["selection_mode"].widget.attrs["data-selection-mode-controller"] = ""
        self.fields["confidence_mode"].widget.attrs[
            "data-confidence-mode-controller"
        ] = ""
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
        for field_name in (
            "feedback_release_policy",
            "confidence_mode",
            "confidence_sampling_unit",
            "confidence_probability",
            "confidence_randomization_seed",
            "confidence_assignment_version",
            "confidence_scale_version",
            "confidence_prompt_version",
            "heartbeat_interval_seconds",
        ):
            if self.is_bound and field_name not in self.data:
                cleaned[field_name] = getattr(self.instance, field_name)
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
        if self.cleaned_data.get("selection_mode") != Experiment.SelectionMode.QUOTA:
            return
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


class ParticipantRuleForm(forms.Form):
    classes = forms.ModelMultipleChoiceField(
        queryset=ClassGroup.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="参与班级",
        required=False,
    )
    includes = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="显式纳入学生",
        required=False,
    )
    excludes = forms.ModelMultipleChoiceField(
        queryset=User.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="从参与班级中排除学生",
        required=False,
    )

    def __init__(self, *args, course, **kwargs):
        super().__init__(*args, **kwargs)
        class_queryset = course.classes.filter(is_active=True).order_by("academic_year", "name", "pk")
        student_queryset = (
            User.objects.filter(
                role=User.Role.STUDENT,
                is_active=True,
                course_enrollments__course=course,
                course_enrollments__is_active=True,
                course_enrollments__class_group__is_active=True,
            )
            .distinct()
            .order_by("student_number", "pk")
        )
        self.fields["classes"].queryset = class_queryset
        self.fields["includes"].queryset = student_queryset
        self.fields["excludes"].queryset = student_queryset

    def clean(self):
        cleaned = super().clean()
        includes = set(cleaned.get("includes") or [])
        excludes = set(cleaned.get("excludes") or [])
        if includes & excludes:
            raise ValidationError("同一学生不能同时显式纳入和排除。")
        return cleaned
