import json

from django import forms
from django.core.exceptions import ValidationError
from django.db import transaction
from .models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from .services.numeric import normalize_finite_decimal
from .services.scoring import normalize_true_false_answer

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
    sequence = forms.IntegerField(label="KC 序号", min_value=1)
    name = forms.CharField(label="KC 名称", max_length=160)
    description = forms.CharField(
        label="说明（可选）", required=False, widget=forms.Textarea(attrs={"rows": 3})
    )

    def __init__(self, *args, course, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.course = course
        self.instance = instance
        if instance is not None:
            self.fields.pop("sequence")
        if instance is None:
            self.fields["sequence"].widget.attrs["id"] = "id_course_concept_sequence"
            self.fields["name"].widget.attrs["id"] = "id_course_concept_name"
            self.fields["description"].widget.attrs["id"] = "id_course_concept_description"
        if instance is not None and not self.is_bound:
            self.initial["name"] = instance.name
            self.initial["description"] = instance.description

    def clean_name(self):
        from .services.course_kc import CourseKCService

        value = " ".join(self.cleaned_data["name"].strip().split())
        try:
            CourseKCService.validate_unique_name(
                course=self.course,
                name=value,
                exclude_pk=self.instance.pk if self.instance is not None else None,
            )
        except ValidationError as exc:
            raise forms.ValidationError(exc.messages[0]) from exc
        return value

    def save(self):
        from .services.course_kc import CourseKCService

        if self.instance is None:
            return CourseKCService.create(
                course=self.course,
                sequence=self.cleaned_data["sequence"],
                name=self.cleaned_data["name"],
                description=self.cleaned_data.get("description", ""),
            )
        return CourseKCService.rename(
            concept=self.instance,
            name=self.cleaned_data["name"],
            description=self.cleaned_data.get("description", ""),
        )

class QuestionEditForm(forms.ModelForm):
    primary_concept = forms.ModelChoiceField(
        label="主 KC（兼容模式）", queryset=Concept.objects.none(), required=False
    )
    secondary_concepts = forms.ModelMultipleChoiceField(
        label="次 KC（兼容模式）", queryset=Concept.objects.none(), required=False
    )
    kc_mapping_json = forms.CharField(required=False, widget=forms.HiddenInput())
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
        concept_queryset = (
            course.concepts.filter(status=Concept.Status.ACTIVE).order_by("code")
            if course is not None
            else Concept.objects.none()
        )
        self.fields["primary_concept"].queryset = concept_queryset
        self.fields["secondary_concepts"].queryset = concept_queryset
        self.fields["question_type"].widget.attrs["data-question-type-controller"] = ""
        if self.instance.pk:
            option_map = {item.key.lower(): item.text for item in self.instance.options.all()}
            for key in "abcdef": self.fields[f"option_{key}"].initial = option_map.get(key, "")
            answer = self.instance.correct_answer
            if self.instance.question_type == Question.Type.TRUE_FALSE:
                self.fields["correct_answer_text"].initial = "正确" if answer is True else "错误"
            else:
                self.fields["correct_answer_text"].initial = ",".join(answer) if isinstance(answer, list) else answer
            self.fields["accepted_answers_text"].initial = ";".join(self.instance.accepted_answers or [])
            from .services.question_kc_mapping import project_question_kc_mapping

            self.fields["kc_mapping_json"].initial = json.dumps(
                project_question_kc_mapping(self.instance), ensure_ascii=False
            )
            primary_link = self.instance.concept_links.filter(is_primary=True).first()
            self.fields["primary_concept"].initial = (
                primary_link.concept_id if primary_link else None
            )
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
                normalize_finite_decimal(raw_answer)
            except ValueError:
                self.add_error("correct_answer_text", "数值题答案必须是有效数字")
        elif question_type == Question.Type.TRUE_FALSE:
            if normalize_true_false_answer(raw_answer) is None:
                self.add_error("correct_answer_text", "判断题答案必须为正确或错误")
        mapping = None
        try:
            mapping = json.loads(cleaned.get("kc_mapping_json") or "{}")
        except (TypeError, ValueError):
            self.add_error("kc_mapping_json", "KC 映射不是有效的结构数据。")
        if mapping is not None:
            primary = cleaned.get("primary_concept")
            secondary = list(cleaned.get("secondary_concepts") or [])
            if primary is not None and primary in secondary:
                self.add_error("secondary_concepts", "主 KC 不能同时作为次 KC。")
            raw_secondary = (
                self.data.getlist("secondary_concepts")
                if hasattr(self.data, "getlist")
                else []
            )
            raw_secondary = [value for value in raw_secondary if value]
            if len(raw_secondary) != len(set(raw_secondary)):
                self.add_error("secondary_concepts", "次 KC 不能重复。")
            if not mapping.get("nodes") and primary is not None:
                mapping = {
                    "schema_version": "question-kc-tree-v2",
                    "root_kc_code": primary.code,
                    "nodes": [
                        {"kc_code": primary.code, "parent_kc_code": None, "order": 1},
                        *[
                            {
                                "kc_code": concept.code,
                                "parent_kc_code": primary.code,
                                "order": index,
                            }
                            for index, concept in enumerate(secondary, start=1)
                            if concept.pk != primary.pk
                        ],
                    ],
                }
            nodes = mapping.get("nodes") or []
            if (
                self.manage_concepts
                and cleaned.get("status") == Question.Status.ACTIVE
                and not nodes
            ):
                self.add_error("kc_mapping_json", "可用题目必须具备完整 KC 树。")
                if not (cleaned.get("kc_mapping_json") or "").strip():
                    self.add_error("primary_concept", "可用题目必须选择主 KC。")
            elif self.manage_concepts and nodes and self.instance.pk:
                from .services.question_kc_mapping import validate_question_kc_mapping

                try:
                    validate_question_kc_mapping(
                        self.instance,
                        root_kc_code=mapping.get("root_kc_code"),
                        nodes=nodes,
                    )
                except forms.ValidationError as exc:
                    self.add_error("kc_mapping_json", exc)
            cleaned["kc_mapping"] = mapping
        return cleaned

    @transaction.atomic
    def save(self, commit=True):
        question = super().save(commit=False)
        raw = self.cleaned_data["correct_answer_text"].strip()
        if question.question_type == Question.Type.MULTIPLE:
            normalized = raw.replace("，", ",").replace("；", ",").replace(";", ",")
            question.correct_answer = sorted(
                {x.strip().upper() for x in normalized.split(",") if x.strip()}
            )
        elif question.question_type == Question.Type.TRUE_FALSE:
            question.correct_answer = normalize_true_false_answer(raw)
        else:
            question.correct_answer = raw.upper() if question.question_type == Question.Type.SINGLE else raw
        if question.question_type in {Question.Type.SHORT, Question.Type.FILL_BLANK}:
            question.accepted_answers = [x.strip() for x in self.cleaned_data["accepted_answers_text"].replace("；", ";").split(";") if x.strip()]
        else:
            question.accepted_answers = []
        if commit:
            desired_status = question.status
            if self.manage_concepts and desired_status == Question.Status.ACTIVE:
                question.status = Question.Status.DRAFT
            question.save()
            question.options.all().delete()
            if question.question_type in {Question.Type.SINGLE, Question.Type.MULTIPLE}:
                for idx, key in enumerate("abcdef", start=1):
                    value = self.cleaned_data.get(f"option_{key}", "").strip()
                    if value: QuestionOption.objects.create(question=question, key=key.upper(), text=value, display_order=idx)
            if self.manage_concepts:
                from .services.question_kc_mapping import replace_question_kc_mapping

                mapping = self.cleaned_data.get("kc_mapping") or {}
                if mapping.get("nodes"):
                    replace_question_kc_mapping(
                        question,
                        root_kc_code=mapping.get("root_kc_code"),
                        nodes=mapping["nodes"],
                    )
                else:
                    question.concept_links.all().delete()
                if desired_status == Question.Status.ACTIVE:
                    question.status = Question.Status.ACTIVE
                    question.save(update_fields=["status"])
        return question
