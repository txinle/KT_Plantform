import json

from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from apps.accounts.models import User


class Command(BaseCommand):
    help = "一次性识别未建立权威状态且仍使用 S + 学号 的学生；默认预演，--apply 不修改密码。"

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        apply = options["apply"]
        counts = {
            "scanned": 0,
            "matched_initial_password": 0,
            "personal_or_unrecognized_password": 0,
            "already_authoritative": 0,
            "errors": 0,
            "would_record": 0,
            "recorded": 0,
        }
        ids = User.objects.filter(role=User.Role.STUDENT).order_by("pk").values_list("pk", flat=True)
        for student_id in ids.iterator(chunk_size=200):
            counts["scanned"] += 1
            try:
                with transaction.atomic():
                    queryset = User.objects.select_for_update() if apply else User.objects
                    student = queryset.filter(pk=student_id, role=User.Role.STUDENT).first()
                    if not student:
                        continue
                    if student.initial_password_state_recorded_at is not None:
                        counts["already_authoritative"] += 1
                        continue
                    matched = bool(
                        not student.is_superuser
                        and student.student_number
                        and check_password(f"S{student.student_number}", student.password)
                    )
                    if apply:
                        student.uses_initial_password = matched
                        student.initial_password_reminder_shown_at = None
                        student.initial_password_state_recorded_at = timezone.now()
                        student.must_change_password = False
                        student.save(update_fields=[
                            "uses_initial_password",
                            "initial_password_reminder_shown_at",
                            "initial_password_state_recorded_at",
                            "must_change_password",
                        ])
                if matched:
                    counts["matched_initial_password"] += 1
                else:
                    counts["personal_or_unrecognized_password"] += 1
                counts["recorded" if apply else "would_record"] += 1
            except Exception:
                counts["errors"] += 1
                self.stderr.write(f"学生内部 ID {student_id} 检查失败，已跳过。")
        self.stdout.write(json.dumps({"mode": "apply" if apply else "dry-run", **counts}))
        if counts["errors"]:
            self.stderr.write("存在未能识别的账号；其密码与显式状态均未修改，可修复后重试。")
