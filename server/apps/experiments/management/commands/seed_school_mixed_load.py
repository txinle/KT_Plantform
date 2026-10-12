import json
import os
import uuid
from io import StringIO
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import CourseTeacher, User
from apps.experiments.models import (
    ClassExperimentTask, Experiment, ExperimentQuestion, ExperimentSamplingKC, PublishPlanClass,
)
from apps.experiments.publication_services import commit_publish_plan, generate_preview, get_or_create_publish_plan


class Command(BaseCommand):
    help = "Create matched baseline/mixed 300-student fixtures and history tasks, only in direct kt_e2e."

    def add_arguments(self, parser):
        parser.add_argument("--password-file", required=True)
        parser.add_argument("--output-dir", required=True)
        parser.add_argument("--run-id", default="")
        parser.add_argument("--capture-protocol", action="store_true")

    def handle(self, *args, **options):
        if connection.settings_dict["NAME"] != "kt_e2e":
            raise CommandError("Only direct isolated database kt_e2e is permitted")
        options['run_id'] = options['run_id'] or uuid.uuid4().hex[:10]
        output = Path(options['output_dir'])
        for suffix in ('baseline', 'mixed', 'baseline-history', 'mixed-history'):
            if (output / f"{options['run_id']}-{suffix}.json").exists():
                raise CommandError('Fixture output already exists; refusing overwrite')
        return self._seed(options)

    @transaction.atomic
    def _seed(self, options):
        password = Path(options["password_file"]).read_text(encoding="utf-8").strip()
        if not password:
            raise CommandError("Password file must contain a fixture password")
        output = Path(options["output_dir"])
        output.mkdir(parents=True, exist_ok=True)
        run = options["run_id"] or uuid.uuid4().hex[:10]
        written = []
        for phase in ("baseline", "mixed"):
            captured = StringIO()
            call_command("seed_assessment_load", run_id=f"{run}-{phase}", students=300,
                         items=20, password=password, feedback_mode=Experiment.Feedback.FULL,
                         capture_protocol=options.get('capture_protocol', False), stdout=captured)
            manifest = json.loads(captured.getvalue())
            task = ClassExperimentTask.objects.select_related("experiment__course", "experiment__owner").get(pk=manifest["task_id"])
            course = task.experiment.course
            teacher = task.experiment.owner
            teachers = [teacher.username]
            for index in range(4):
                additional = User.objects.create_user(username=f"load-teacher-{run}-{phase}-{index}",
                                                      password=password, role=User.Role.TEACHER)
                CourseTeacher.objects.create(course=course, teacher=additional)
                teachers.append(additional.username)
            history = Experiment.objects.create(course=course, owner=teacher, created_by=teacher,
                question_bank=task.experiment.question_bank, name=f"HTTP history {run} {phase}",
                sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=20,
                feedback_mode=Experiment.Feedback.FULL,
                feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
                research_tools=task.experiment.research_tools)
            ExperimentQuestion.objects.bulk_create([
                ExperimentQuestion(experiment=history, question_id=row.question_id, display_order=row.display_order)
                for row in ExperimentQuestion.objects.filter(experiment=task.experiment)])
            ExperimentSamplingKC.objects.bulk_create([
                ExperimentSamplingKC(experiment=history, concept_id=row.concept_id)
                for row in ExperimentSamplingKC.objects.filter(experiment=task.experiment)])
            plan = get_or_create_publish_plan(history, teacher)
            PublishPlanClass.objects.create(publish_plan=plan, class_group_id=task.class_group_id)
            generate_preview(plan, teacher, expected_revision=plan.plan_revision)
            plan.refresh_from_db()
            history_task = commit_publish_plan(plan, teacher, expected_revision=plan.plan_revision)[0]
            manifest.update(database="kt_e2e", course_id=course.pk, class_id=task.class_group_id,
                            experiment_id=task.experiment_id, history_experiment_id=history.pk,
                            question_id=ExperimentQuestion.objects.filter(experiment=task.experiment).order_by("display_order").values_list("question_id", flat=True).first(),
                            teacher_usernames=teachers)
            for suffix, body in ((phase, manifest), (f"{phase}-history", {**manifest, "task_id": history_task.pk, "experiment_id": history.pk})):
                path = output / f"{run}-{suffix}.json"
                with path.open("x", encoding="utf-8") as handle:
                    json.dump(body, handle, ensure_ascii=False, indent=2)
                os.chmod(path, 0o600)
                written.append(str(path))
        self.stdout.write(json.dumps({"database": "kt_e2e", "fixture_files": written, "contains_credentials": True}))
