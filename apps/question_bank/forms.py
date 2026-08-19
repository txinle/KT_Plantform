from django import forms
from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption

class QuestionBankForm(forms.ModelForm):
    class Meta:
        model = QuestionBank
        fields = ["name", "description", "visibility"]
        labels = {"name": "题库名称", "description": "说明", "visibility": "可见性"}
        widgets = {"description": forms.Textarea(attrs={"rows": 4})}


class QuestionBankVisibilityForm(forms.ModelForm):
    class Meta:
        model = QuestionBank
        fields = ["visibility"]

class UploadForm(forms.Form):
    file = forms.FileField(label="CSV 或 Excel 文件")


class CourseConceptForm(forms.Form):
    name = forms.CharField(label="KC 名称", max_length=160)

    def __init__(self, *args, course, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.instance = instance
        if instance is None:
            self.fields["name"].widget.attrs["id"] = "id_course_concept_name"
        if instance is not None and not self.is_bound:
            self.initial["name"] = instance.name

    def clean_name(self):
        value = " ".join(self.cleaned_data["name"].strip().split())
        if not value:
            raise forms.ValidationError("KC 名称不能为空。")
        normalized = value.casefold()
        duplicate = Concept.objects.filter(course=self.course, normalized_name=normalized)
        if self.instance is not None:
            duplicate = duplicate.exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("当前课程已存在同名 KC。")
        return value

    def save(self):
        if self.instance is None:
            return Concept.objects.create(course=self.course, name=self.cleaned_data["name"])
        self.instance.name = self.cleaned_data["name"]
        self.instance.save(update_fields=["name", "normalized_name"])
        return self.instance

class QuestionEditForm(forms.ModelForm):
    primary_concept = forms.ModelChoiceField(
        label="主 KC",
        queryset=Concept.objects.none(),
        required=False,
    )
    secondary_concepts = forms.ModelMultipleChoiceField(
        label="次 KC",
        queryset=Concept.objects.none(),
        required=False,
    )
    accepted_answers_text = forms.CharField(label="其他可接受答案（分号分隔）", required=False)
    option_a = forms.CharField(label="选项 A", required=False)
    option_b = forms.CharField(label="选项 B", required=False)
    option_c = forms.CharField(label="选项 C", required=False)
    option_d = forms.CharField(label="选项 D", required=False)
    option_e = forms.CharField(label="选项 E", required=False)
    option_f = forms.CharField(label="选项 F", required=False)
    correct_answer_text = forms.CharField(label="正确答案")
    class Meta:
        model = Question
        fields = ["text", "question_type", "teacher_difficulty", "absolute_tolerance", "relative_tolerance", "explanation", "status"]
        labels = {
            "text": "题干",
            "question_type": "题型",
            "teacher_difficulty": "教师设定难度",
            "absolute_tolerance": "绝对误差容限",
            "relative_tolerance": "相对误差容限",
            "explanation": "解析",
            "status": "题目状态",
        }
        widgets = {"text": forms.Textarea(attrs={"rows": 4}), "explanation": forms.Textarea(attrs={"rows": 3})}
    def __init__(self, *args, course=None, **kwargs):
        super().__init__(*args, **kwargs)
        if course is None and self.instance.pk:
            course = self.instance.bank.course
        self.manage_concepts = course is not None
        concept_queryset = course.concepts.order_by("name") if course is not None else Concept.objects.none()
        self.fields["primary_concept"].required = self.manage_concepts
        self.fields["primary_concept"].queryset = concept_queryset
        self.fields["secondary_concepts"].queryset = concept_queryset
        self.fields["primary_concept"].widget.attrs["data-concept-select"] = "primary"
        self.fields["secondary_concepts"].widget.attrs["data-concept-select"] = "secondary"
        self.fields["question_type"].widget.attrs["data-question-type-controller"] = ""
        if self.instance.pk:
            option_map = {item.key.lower(): item.text for item in self.instance.options.all()}
            for key in "abcdef": self.fields[f"option_{key}"].initial = option_map.get(key, "")
            answer = self.instance.correct_answer
            self.fields["correct_answer_text"].initial = ",".join(answer) if isinstance(answer, list) else answer
            self.fields["accepted_answers_text"].initial = ";".join(self.instance.accepted_answers or [])
            primary_link = self.instance.concept_links.filter(is_primary=True).first()
            self.fields["primary_concept"].initial = primary_link.concept_id if primary_link else None
            self.fields["secondary_concepts"].initial = self.instance.concept_links.filter(
                is_primary=False
            ).values_list("concept_id", flat=True)
    def clean(self):
        cleaned = super().clean()
        question_type = cleaned.get("question_type")
        raw_answer = (cleaned.get("correct_answer_text") or "").strip()
        option_values = {
            key.upper(): (cleaned.get(f"option_{key}") or "").strip()
            for key in "abcdef"
        }
        available = {key for key, value in option_values.items() if value}
        if question_type in {Question.Type.SINGLE, Question.Type.MULTIPLE}:
            answers = {
                part.strip().upper()
                for part in raw_answer.replace("，", ",").replace("；", ",").replace(";", ",").split(",")
                if part.strip()
            }
            if not available:
                self.add_error("option_a", "选择题至少需要一个选项")
            invalid = sorted(answers - available)
            if invalid:
                self.add_error("correct_answer_text", f"答案引用了不存在的选项：{', '.join(invalid)}")
            if question_type == Question.Type.SINGLE and len(answers) != 1:
                self.add_error("correct_answer_text", "单选题必须且只能填写一个正确选项")
            if question_type == Question.Type.MULTIPLE and len(answers) < 2:
                self.add_error("correct_answer_text", "多选题至少需要两个正确选项")
        elif question_type == Question.Type.NUMERIC:
            try:
                float(raw_answer)
            except (TypeError, ValueError):
                self.add_error("correct_answer_text", "数值题答案必须是有效数字")
        primary = cleaned.get("primary_concept")
        secondary = cleaned.get("secondary_concepts")
        if primary is not None and secondary is not None and primary in secondary:
            self.add_error("secondary_concepts", "主 KC 不能同时作为次 KC。")
        raw_secondary = self.data.getlist("secondary_concepts") if hasattr(self.data, "getlist") else []
        raw_secondary = [value for value in raw_secondary if value]
        if len(raw_secondary) != len(set(raw_secondary)):
            self.add_error("secondary_concepts", "次 KC 不能重复。")
        return cleaned

    def save(self, commit=True):
        question = super().save(commit=False)
        raw = self.cleaned_data["correct_answer_text"].strip()
        if question.question_type == Question.Type.MULTIPLE:
            question.correct_answer = sorted({x.strip().upper() for x in raw.replace("，", ",").split(",") if x.strip()})
        else:
            question.correct_answer = raw.upper() if question.question_type == Question.Type.SINGLE else raw
        question.accepted_answers = [x.strip() for x in self.cleaned_data["accepted_answers_text"].replace("；", ";").split(";") if x.strip()]
        if commit:
            question.save()
            question.options.all().delete()
            for idx, key in enumerate("abcdef", start=1):
                value = self.cleaned_data.get(f"option_{key}", "").strip()
                if value: QuestionOption.objects.create(question=question, key=key.upper(), text=value, display_order=idx)
            if self.manage_concepts:
                question.concept_links.all().delete()
                primary = self.cleaned_data.get("primary_concept")
                if primary is not None:
                    QuestionConcept.objects.create(question=question, concept=primary, is_primary=True)
                QuestionConcept.objects.bulk_create(
                    [
                        QuestionConcept(question=question, concept=concept, is_primary=False)
                        for concept in self.cleaned_data.get("secondary_concepts", [])
                    ]
                )
        return question
