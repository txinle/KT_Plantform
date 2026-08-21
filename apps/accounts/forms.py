import hashlib

from django import forms
from django.core import signing
from django.db import transaction
from django.utils.crypto import constant_time_compare

from .models import ClassGroup, Course, CourseTeacher, User


CLASS_GROUP_STATE_TOKEN_SALT = "accounts.class_group_state"
COURSE_SETTINGS_STATE_TOKEN_SALT = "accounts.course_settings_state"


def _class_group_state_digest(class_group):
    serialized_state = "\x1f".join(
        [
            str(class_group.pk),
            class_group.name,
            class_group.academic_year,
            str(class_group.is_active),
        ]
    )
    return hashlib.sha256(serialized_state.encode("utf-8")).hexdigest()


def class_group_state_token(class_group):
    return signing.dumps(
        {
            "class_group_id": class_group.pk,
            "state_digest": _class_group_state_digest(class_group),
        },
        salt=CLASS_GROUP_STATE_TOKEN_SALT,
    )


def class_group_state_token_matches(token, class_group):
    try:
        payload = signing.loads(token, salt=CLASS_GROUP_STATE_TOKEN_SALT)
    except signing.BadSignature:
        return False
    return payload.get("class_group_id") == class_group.pk and constant_time_compare(
        payload.get("state_digest", ""),
        _class_group_state_digest(class_group),
    )


def _course_settings_state_digest(course):
    teacher_ids = ",".join(
        str(value)
        for value in course.teacher_links.order_by("teacher_id").values_list(
            "teacher_id", flat=True
        )
    )
    serialized_state = "\x1f".join(
        [
            str(course.pk),
            course.name,
            course.code,
            course.term,
            course.description,
            course.status,
            teacher_ids,
        ]
    )
    return hashlib.sha256(serialized_state.encode("utf-8")).hexdigest()


def course_settings_state_token(course):
    return signing.dumps(
        {"course_id": course.pk, "state_digest": _course_settings_state_digest(course)},
        salt=COURSE_SETTINGS_STATE_TOKEN_SALT,
    )


def course_settings_state_token_matches(token, course):
    try:
        payload = signing.loads(token, salt=COURSE_SETTINGS_STATE_TOKEN_SALT)
    except signing.BadSignature:
        return False
    return payload.get("course_id") == course.pk and constant_time_compare(
        payload.get("state_digest", ""), _course_settings_state_digest(course)
    )


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["name", "code", "term", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class CourseSettingsForm(CourseForm):
    state_token = forms.CharField(
        required=True,
        widget=forms.HiddenInput,
        error_messages={"required": "页面状态已过期，请刷新后重试。"},
    )
    teachers = forms.ModelMultipleChoiceField(
        label="协作教师",
        queryset=User.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["teachers"].queryset = User.objects.filter(
            role=User.Role.TEACHER,
            is_active=True,
        ).exclude(pk=self.instance.owner_id)
        if not self.is_bound:
            self.initial["teachers"] = self.instance.teacher_links.values_list(
                "teacher_id",
                flat=True,
            )
            self.initial["state_token"] = course_settings_state_token(self.instance)

    @transaction.atomic
    def save(self, commit=True):
        course = super().save(commit=commit)
        if commit:
            course.teacher_links.all().delete()
            CourseTeacher.objects.bulk_create(
                [CourseTeacher(course=course, teacher=teacher) for teacher in self.cleaned_data["teachers"]]
            )
        return course


class ClassGroupForm(forms.ModelForm):
    class Meta:
        model = ClassGroup
        fields = ["name", "academic_year"]


class ClassGroupRenameForm(forms.ModelForm):
    class Meta:
        model = ClassGroup
        fields = ["name"]

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise forms.ValidationError("班级名称不能为空。")
        duplicate = ClassGroup.objects.filter(
            course=self.instance.course,
            academic_year=self.instance.academic_year,
            name=value,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("同一课程和学年中已存在该班级名称。")
        return value


class ClassGroupUpdateForm(forms.ModelForm):
    state_token = forms.CharField(
        required=True,
        widget=forms.HiddenInput,
        error_messages={"required": "页面状态已过期，请刷新后重试。"},
    )

    class Meta:
        model = ClassGroup
        fields = ["name", "academic_year", "is_active"]
        labels = {
            "name": "班级名称",
            "academic_year": "学年",
            "is_active": "班级可用",
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if not self.is_bound and self.instance.pk:
            self.initial["state_token"] = class_group_state_token(self.instance)

    def clean_name(self):
        value = self.cleaned_data["name"].strip()
        if not value:
            raise forms.ValidationError("班级名称不能为空。")
        return value

    def clean_academic_year(self):
        return self.cleaned_data["academic_year"].strip()

    def clean(self):
        cleaned = super().clean()
        name = cleaned.get("name")
        academic_year = cleaned.get("academic_year")
        if name is None or academic_year is None:
            return cleaned
        duplicate = ClassGroup.objects.filter(
            course=self.instance.course,
            name=name,
            academic_year=academic_year,
        ).exclude(pk=self.instance.pk)
        if duplicate.exists():
            raise forms.ValidationError("同一课程和学年中已存在该班级名称。")
        return cleaned


class StudentCreateForm(forms.Form):
    student_number = forms.CharField(label="学号", max_length=64)
    display_name = forms.CharField(label="姓名", max_length=80)
    class_group = forms.ModelChoiceField(label="班级", queryset=ClassGroup.objects.none())
    password = forms.CharField(label="初始密码", widget=forms.PasswordInput, min_length=8)

    def __init__(self, *args, owner=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.owner = owner
        classes = ClassGroup.objects.filter(is_active=True)
        if owner and not (owner.is_superuser or owner.role == User.Role.ADMIN):
            classes = classes.filter(owner=owner)
        self.fields["class_group"].queryset = classes

    def clean_student_number(self):
        value = self.cleaned_data["student_number"].strip()
        if User.objects.filter(student_number=value).exists() or User.objects.filter(username=value).exists():
            raise forms.ValidationError("该学号已存在")
        return value

    @transaction.atomic
    def save(self):
        number = self.cleaned_data["student_number"]
        return User.objects.create_user(
            username=number,
            student_number=number,
            display_name=self.cleaned_data["display_name"].strip(),
            class_group=self.cleaned_data["class_group"],
            role=User.Role.STUDENT,
            password=self.cleaned_data["password"],
        )


class PasswordResetForm(forms.Form):
    password = forms.CharField(label="新密码", widget=forms.PasswordInput, min_length=8)


class StudentBatchUploadForm(forms.Form):
    file = forms.FileField(label="Excel 学生名单")


class ManualStudentEnrollmentForm(forms.Form):
    student_number = forms.CharField(label="学号", max_length=64)
    display_name = forms.CharField(label="姓名", max_length=80)

    def clean_student_number(self):
        value = self.cleaned_data["student_number"].strip()
        if not value:
            raise forms.ValidationError("学号不能为空。")
        return value

    def clean_display_name(self):
        value = self.cleaned_data["display_name"].strip()
        if not value:
            raise forms.ValidationError("姓名不能为空。")
        return value


class SourceClassChoiceField(forms.ModelChoiceField):
    def label_from_instance(self, obj):
        return f"{obj.course} / {obj}"


class StudentClassCopyForm(forms.Form):
    source_class = SourceClassChoiceField(
        label="来源课程班级",
        queryset=ClassGroup.objects.none(),
        empty_label="请选择来源课程班级",
        error_messages={"invalid_choice": "请选择一个有效的选项。"},
    )

    def __init__(self, *args, source_classes=None, **kwargs):
        super().__init__(*args, **kwargs)
        if source_classes is not None:
            self.fields["source_class"].queryset = source_classes
