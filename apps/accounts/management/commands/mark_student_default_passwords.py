import json

from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import User


class Command(BaseCommand):
    help = "识别仍使用 S + 学号 的学生密码；默认只预演，--apply 仅标记强制改密。"

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument("--dry-run", action="store_true")
        mode.add_argument("--apply", action="store_true")

    def handle(self, *args, **options):
        apply = options["apply"]
        counts = {"scanned": 0, "matched": 0, "skipped": 0, "errors": 0, "would_mark": 0, "marked": 0}
        ids = User.objects.filter(role=User.Role.STUDENT).order_by("pk").values_list("pk", flat=True)
        for student_id in ids.iterator(chunk_size=200):
            counts["scanned"] += 1
            try:
                changed = False
                with transaction.atomic():
                    queryset = User.objects.select_for_update() if apply else User.objects
                    student = queryset.filter(pk=student_id, role=User.Role.STUDENT).first()
                    matched = bool(student and not student.is_superuser and student.student_number and check_password(f"S{student.student_number}", student.password))
                    if matched and not student.must_change_password:
                        if apply:
                            User.objects.filter(pk=student.pk).update(must_change_password=True)
                        changed = True
                if matched:
                    counts["matched"] += 1
                    if changed:
                        counts["marked" if apply else "would_mark"] += 1
                else:
                    counts["skipped"] += 1
            except Exception:
                counts["errors"] += 1
                self.stderr.write(f"学生内部 ID {student_id} 检查失败，已跳过。")
        self.stdout.write(json.dumps({"mode": "apply" if apply else "dry-run", **counts}))
        if counts["errors"]:
            self.stderr.write("存在异常账号，请检查后再重试；未修改其密码。")
