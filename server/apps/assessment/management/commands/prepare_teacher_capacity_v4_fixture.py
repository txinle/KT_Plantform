"""Create a new isolated baseline and three independently authenticated teachers."""
import json
import os
from pathlib import Path
import re

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.accounts.models import CourseTeacher, User
from .prepare_student_capacity_baseline import require_isolated_database
from .seed_prepared_startup_load import TEST_PASSWORD, _private_stream


class Command(BaseCommand):
    def add_arguments(self, parser):
        parser.add_argument("--cell-id", required=True)
        parser.add_argument("--directory", required=True)

    def handle(self, *args, **options):
        require_isolated_database()
        if os.getenv("TEACHER_CAPACITY_PROJECT") != "kt-school-teacher-capacity-20261018":
            raise CommandError("Only the new explicitly owned teacher capacity project is permitted")
        cell = options["cell_id"]
        if not re.fullmatch("[a-f0-9]{20}", cell):
            raise CommandError("Frozen v4 cell ID required")
        directory = Path(options["directory"]).resolve()
        if not directory.is_relative_to(Path("/app/media/teacher-capacity-v4")):
            raise CommandError("Fixture must stay inside this project's media volume")
        directory.mkdir(parents=True, mode=0o700, exist_ok=False)
        source, baseline = directory / "source.json", directory / "fixture.json"
        call_command("seed_prepared_startup_load", run_id=cell, scenario="same-task", questions=20,
                     students=180, output=str(source))
        call_command("prepare_student_capacity_baseline", source_fixture=str(source), baseline_id=cell,
                     run_id=cell + "-b", output=str(baseline))
        fixture = json.loads(baseline.read_text())
        owner = User.objects.get(pk=fixture["source_ownership"]["teacher_id"])
        teachers = [{"user_id": owner.pk, "username": owner.username, "password": TEST_PASSWORD}]
        with transaction.atomic():
            for index in (2, 3):
                teacher = User.objects.create_user(username=f"tv4-{cell}-teacher-{index}",
                                                  password=TEST_PASSWORD, role=User.Role.TEACHER)
                teachers.append({"user_id": teacher.pk, "username": teacher.username, "password": TEST_PASSWORD})
                for task in fixture["tasks"]:
                    CourseTeacher.objects.create(course_id=task["course_id"], teacher=teacher)
        with _private_stream(directory / "teachers.json") as stream:
            json.dump({"project": os.environ["TEACHER_CAPACITY_PROJECT"], "cell_id": cell,
                       "accounts": teachers, "scope": fixture["tasks"][0]}, stream)
        self.stdout.write(json.dumps({"cell_id": cell, "fixture": str(baseline),
                                      "teachers": str(directory / "teachers.json"), "count": 180}))
