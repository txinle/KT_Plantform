import json

from django.core.management.base import BaseCommand, CommandError

from apps.accounts.models import Course
from apps.question_bank.services.course_question_identity import (
    CourseQuestionRenumberingBlocked,
    renumber_course_question_identities,
)


class Command(BaseCommand):
    help = "检查课程题号并按原 course_sequence 顺序压缩为连续编号。默认仅报告。"

    def add_arguments(self, parser):
        parser.add_argument("course_ids", nargs="*", type=int)
        parser.add_argument(
            "--apply",
            action="store_true",
            help="应用修复；未提供时只输出 dry-run 报告。",
        )

    def handle(self, *args, **options):
        course_ids = list(dict.fromkeys(options["course_ids"]))
        courses = Course.objects.order_by("pk")
        if course_ids:
            courses = courses.filter(pk__in=course_ids)
            found = set(courses.values_list("pk", flat=True))
            missing = [str(course_id) for course_id in course_ids if course_id not in found]
            if missing:
                raise CommandError(f"课程不存在：{', '.join(missing)}")
        else:
            courses = courses.filter(question_identities__isnull=False).distinct()

        for course in courses:
            try:
                report = renumber_course_question_identities(
                    course,
                    apply=options["apply"],
                    reason="course_question_numbers_repaired",
                )
            except CourseQuestionRenumberingBlocked as exc:
                report = exc.report or {
                    "course_id": course.pk,
                    "status": "blocked",
                    "dry_run": False,
                    "mappings": [],
                    "removed_identity_ids": [],
                    "affected_question_ids": [],
                    "blocking_experiments": exc.experiment_names,
                    "export_count": 0,
                    "cleanup_pending": False,
                    "cleanup_id": None,
                }
            self.stdout.write(json.dumps(report, ensure_ascii=False, sort_keys=True))
