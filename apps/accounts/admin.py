from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import (
    ClassGroup,
    Course,
    CourseEnrollment,
    CourseSubject,
    CourseTeacher,
    ResearchStudy,
    ResearchStudyCourse,
    ResearchStudyParticipant,
    User,
)

admin.site.site_header = "KT Data Lab 管理后台"
admin.site.site_title = "KT Data Lab 管理后台"
admin.site.index_title = "平台数据管理"

User._meta.verbose_name = "用户账号"
User._meta.verbose_name_plural = "用户账号"
Course._meta.verbose_name = "课程"
Course._meta.verbose_name_plural = "课程"
CourseTeacher._meta.verbose_name = "协作教师"
CourseTeacher._meta.verbose_name_plural = "协作教师"
ClassGroup._meta.verbose_name = "课程班级"
ClassGroup._meta.verbose_name_plural = "课程班级"
CourseEnrollment._meta.verbose_name = "课程选课"
CourseEnrollment._meta.verbose_name_plural = "课程选课"

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number")}),)
    list_display = ("username", "display_name", "role", "student_number", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("username", "display_name", "student_number")
    ordering = ("username",)

@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "course", "is_active")
    list_filter = ("course", "is_active")
    search_fields = ("name", "academic_year", "course__name", "course__code")

    def get_readonly_fields(self, request, obj=None):
        readonly_fields = tuple(super().get_readonly_fields(request, obj))
        if (
            obj is not None
            and obj.experiment_tasks.exists()
            and "course" not in readonly_fields
        ):
            return (*readonly_fields, "course")
        return readonly_fields


class CourseTeacherInline(admin.TabularInline):
    model = CourseTeacher
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "term", "owner", "status")
    list_filter = ("status", "term")
    search_fields = ("name", "code", "owner__username")
    inlines = [CourseTeacherInline]
    date_hierarchy = "created_at"


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "class_group", "is_active")
    list_filter = ("course", "class_group", "is_active")
    search_fields = ("student__username", "student__student_number", "student__display_name")


@admin.register(CourseSubject)
class CourseSubjectAdmin(admin.ModelAdmin):
    list_display = ("public_uuid", "course", "student", "created_at")
    list_filter = ("course",)
    search_fields = ("public_uuid", "student__student_number", "student__display_name")
    readonly_fields = ("public_uuid", "created_at")


class ResearchStudyCourseInline(admin.TabularInline):
    model = ResearchStudyCourse
    extra = 0


class ResearchStudyParticipantInline(admin.TabularInline):
    model = ResearchStudyParticipant
    extra = 0
    readonly_fields = ("public_uuid", "created_at")


@admin.register(ResearchStudy)
class ResearchStudyAdmin(admin.ModelAdmin):
    list_display = ("name", "public_uuid", "owner", "is_active", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("name", "public_uuid", "owner__username")
    readonly_fields = ("public_uuid", "created_at", "updated_at")
    inlines = (ResearchStudyCourseInline, ResearchStudyParticipantInline)
