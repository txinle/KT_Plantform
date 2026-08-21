from django.contrib import admin
from .models import AuditLog

AuditLog._meta.verbose_name = "审计日志"
AuditLog._meta.verbose_name_plural = "审计日志"


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("created_at", "actor", "action", "object_type", "object_id", "ip_address")
    list_filter = ("action", "object_type")
    search_fields = ("actor__username", "action", "object_id")
    readonly_fields = ("actor", "action", "object_type", "object_id", "details", "ip_address", "created_at")
