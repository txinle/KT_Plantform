from django import forms
from .models import Question, QuestionBank, QuestionOption

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

class QuestionEditForm(forms.ModelForm):
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
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["question_type"].widget.attrs["data-question-type-controller"] = ""
        if self.instance.pk:
            option_map = {item.key.lower(): item.text for item in self.instance.options.all()}
            for key in "abcdef": self.fields[f"option_{key}"].initial = option_map.get(key, "")
            answer = self.instance.correct_answer
            self.fields["correct_answer_text"].initial = ",".join(answer) if isinstance(answer, list) else answer
            self.fields["accepted_answers_text"].initial = ";".join(self.instance.accepted_answers or [])
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
        return question
