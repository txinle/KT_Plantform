from django import forms
from django.db import transaction

from .models import ClassGroup, Course, CourseTeacher, User


class CourseForm(forms.ModelForm):
    class Meta:
        model = Course
        fields = ["name", "code", "term", "description"]
        widgets = {"description": forms.Textarea(attrs={"rows": 3})}


class CourseSettingsForm(CourseForm):
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
