from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import ClassGroup, User

@admin.register(User)
class CustomUserAdmin(UserAdmin):
    fieldsets = UserAdmin.fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number", "class_group")}),)
    add_fieldsets = UserAdmin.add_fieldsets + (("平台信息", {"fields": ("role", "display_name", "student_number", "class_group")}),)
    list_display = ("username", "display_name", "role", "student_number", "class_group", "is_active")
    list_filter = ("role", "class_group", "is_active")
    search_fields = ("username", "display_name", "student_number")

@admin.register(ClassGroup)
class ClassGroupAdmin(admin.ModelAdmin):
    list_display = ("name", "academic_year", "owner", "is_active")
    search_fields = ("name", "academic_year")
