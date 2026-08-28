import json

from django.core.management.base import BaseCommand

from apps.experiments.class_task_audit import audit_class_experiment_tasks


class Command(BaseCommand):
    help = "审计无法安全映射为正式班级实验任务的历史发布记录。"

    def add_arguments(self, parser):
        parser.add_argument(
            "--format",
            choices=("table", "json"),
            default="table",
            help="输出格式（默认：table）。",
        )

    def handle(self, *args, **options):
        rows = audit_class_experiment_tasks()
        if options["format"] == "json":
            payload = [row.as_dict() for row in rows]
            self.stdout.write(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
            )
            return
        self.stdout.write(_render_table(rows))


def _table_value(value):
    return str(value).replace("\r", " ").replace("\n", " ")


def _render_table(rows):
    headers = (
        "课程",
        "班级",
        "实验",
        "发布快照",
        "班级快照",
        "参与者",
        "Session",
        "Attempt",
        "GradeRecord",
        "冲突原因",
    )
    values = [
        (
            _table_value(row.course_label),
            _table_value(row.class_label or "未映射"),
            _table_value(row.experiment_label),
            str(row.release_snapshot_count),
            str(row.class_snapshot_count),
            str(row.participant_count),
            str(row.session_count),
            str(row.attempt_count),
            str(row.grade_record_count),
            ",".join(row.reason_codes),
        )
        for row in rows
    ]
    widths = [
        max(len(header), *(len(value[index]) for value in values))
        if values
        else len(header)
        for index, header in enumerate(headers)
    ]

    def render(values):
        return "| " + " | ".join(
            value.ljust(widths[index]) for index, value in enumerate(values)
        ) + " |"

    separator = "|-" + "-|-".join("-" * width for width in widths) + "-|"
    lines = [render(headers), separator]
    lines.extend(render(value) for value in values)
    return "\n".join(lines)
