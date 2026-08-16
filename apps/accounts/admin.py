from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import ClassGroup, Course, CourseEnrollment, CourseTeacher, User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number")}),)
    list_display = ("username", "display_name", "role", "student_number", "is_active")
    list_filter = ("role", "is_active")
    search_fields = ("username", "display_name", "student_number")

@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "course", "is_active")
    list_filter = ("course", "is_active")
    search_fields = ("name", "academic_year", "course__name", "course__code")


class CourseTeacherInline(admin.TabularInline):
    model = CourseTeacher
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "term", "owner", "status")
    list_filter = ("status", "term")
    search_fields = ("name", "code", "owner__username")
    inlines = [CourseTeacherInline]


@admin.register(CourseEnrollment)
class CourseEnrollmentAdmin(admin.ModelAdmin):
    list_display = ("student", "course", "class_group", "is_active")
    list_filter = ("course", "class_group", "is_active")
    search_fields = ("student__username", "student__student_number", "student__display_name")
