import json

from django.core.management.base import CommandError
from django.db import connection, transaction
from django.utils import timezone

from apps.accounts.models import User
from apps.question_bank.models import Question

from .seed_e2e_capture_v2 import Command as CaptureV2SeedCommand


class Command(CaptureV2SeedCommand):
    help = "Create an isolated six-item capture initialization browser fixture in kt_e2e."
    source_types = (
        ("单选题", Question.Type.SINGLE),
        ("判断题", Question.Type.TRUE_FALSE),
        ("多选题", Question.Type.MULTIPLE),
        ("填空题", Question.Type.FILL_BLANK),
        ("简答题", Question.Type.SHORT),
        ("数值题", Question.Type.NUMERIC),
    )
    leaf_codes = ("KC2", "KC2", "KC3", "KC4", "KC5", "KC5")
    course_description = "Isolated browser fixture for capture initialization and six response types."
    bank_description = "First item of all six response types from eda_chapter1_193.csv."
    experiment_description = "One item for each supported response type."

    def add_arguments(self, parser):
        parser.add_argument("--run-key", required=True)

    def handle(self, *args, **options):
        if str(connection.settings_dict["NAME"]) != "kt_e2e":
            raise CommandError("seed_e2e_capture_initialization only runs against kt_e2e.")
        run_key = options["run_key"].strip()
        if not run_key or len(run_key) > 40 or not all(char.isascii() and (char.isalnum() or char in "-_") for char in run_key):
            raise CommandError("run-key must be 1-40 ASCII letters, digits, hyphens, or underscores.")
        self.run_key = run_key
        self.course_code = f"CAPINIT-{run_key}"
        self.course_term = "E2E-CAPTURE-INIT"
        self.class_name = f"初始化测试班-{run_key}"
        self.experiment_name = f"E2E Capture 初始化六题-{run_key}"
        self.bank_name = f"E2E Capture 初始化题库-{run_key}"
        self.course_name = f"Capture 初始化 E2E {run_key}"
        with transaction.atomic():
            payload = self._seed()
        self.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True))

    def _student(self, class_group):
        username = f"CI-{self.run_key}"
        student, _ = User.objects.get_or_create(
            username=username,
            defaults={
                "role": User.Role.STUDENT,
                "student_number": username,
                "display_name": f"初始化测试学生 {self.run_key}",
                "class_group": class_group,
                "is_active": True,
            },
        )
        student.role = User.Role.STUDENT
        student.student_number = username
        student.class_group = class_group
        student.is_active = True
        student.set_password("student123")
        student.uses_initial_password = False
        student.initial_password_reminder_shown_at = None
        student.initial_password_state_recorded_at = timezone.now()
        student.must_change_password = False
        student.save()
        return student

    def _source_rows(self):
        rows = super()._source_rows()
        rows["数值题"] = {
            "序号": "194",
            "题型": "数值题",
            "题目": "12.5 的数值是多少？",
            "答案": "12.5",
            "可接受答案": "",
            "解析 ": "数值应为 12.5。",
            **{f"选项{key}": "" for key in "ABCDEF"},
        }
        return rows

    @staticmethod
    def _research_tools(_questions):
        return {
            "doubt_enabled": False,
            "check_enabled": False,
            "kc_visible": True,
            "hints": [],
            "rules": [],
        }

    @staticmethod
    def _payload(task, student):
        payload = CaptureV2SeedCommand._payload(task, student)
        payload["experiment_name"] = task.experiment.name
        payload["student"]["password"] = "student123"
        return payload
