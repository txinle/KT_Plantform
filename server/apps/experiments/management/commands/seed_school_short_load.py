import json
import os
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from apps.assessment.models import ExperimentSession

from apps.accounts.models import Course, ClassGroup, CourseEnrollment, CourseEnrollmentAccessPeriod, CourseSubject, User
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from apps.question_bank.services.course_question_identity import allocate_course_question_identity
from apps.experiments.models import ClassExperimentTask, Experiment, ExperimentQuestion, ExperimentSamplingKC, PublishPlanClass
from apps.experiments.publication_services import get_or_create_publish_plan, generate_preview, commit_publish_plan
from .seed_assessment_load import protocol_question_spec, protocol_tools


class Command(BaseCommand):
    help = 'Publish separate 3/50/150/300 short-load tasks using the retained baseline roster; kt_e2e only.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--output-dir', required=True)
        parser.add_argument('--source-config', required=True)
        parser.add_argument('--capture-protocol', action='store_true', help='Separate five-type protocol tasks; leaves legacy short regression unchanged')

    def handle(self, *args, **options):
        if connection.settings_dict['NAME'] != 'kt_e2e':
            raise CommandError('Only direct isolated database kt_e2e is permitted')
        return self._seed(options)

    @transaction.atomic
    def _seed(self, options, sizes=(3, 50, 150, 300)):
        fixture = json.loads(Path(options['manifest']).read_text())
        config = json.loads(Path(options['source_config']).read_text())
        if fixture.get('database') != 'kt_e2e' or len(fixture['usernames']) != 300 or fixture['item_count'] != 20:
            raise CommandError('Retained 300-student/20-question baseline required')
        original = ClassExperimentTask.objects.select_related('experiment__course', 'experiment__owner').get(pk=fixture['task_id'])
        if original.sessions.exclude(status=ExperimentSession.Status.PREPARED).exists():
            raise CommandError('Formal baseline task must remain cold')
        roster = User.objects.in_bulk(fixture['usernames'], field_name='username')
        if len(roster) != 300 or any(user.role != User.Role.STUDENT for user in roster.values()):
            raise CommandError('Existing baseline roster required')
        questions = list(Question.objects.filter(experimentquestion__experiment=original.experiment).order_by('sequence').prefetch_related('options', 'concept_links__concept'))
        protocol = options.get('capture_protocol', False)
        if len(questions) != 20 or (not protocol and any(question.question_type != Question.Type.SINGLE or question.correct_answer != 'A' for question in questions)):
            raise CommandError('Expected baseline single-choice bank required')
        owner = original.experiment.owner
        run = fixture['run_id'] + ('-protocol-v1' if protocol else '-short-v7')
        output = Path(options['output_dir'])
        output.mkdir(mode=0o700)
        paths = []
        for count in sizes:
            code = 'SHORT-' + run + '-' + str(count)
            if Course.objects.filter(code=code).exists():
                raise CommandError('Short fixture already exists; refusing reuse')
            course = Course.objects.create(owner=owner, name=f'Short storm {count}', code=code, term='capacity')
            group = ClassGroup.objects.create(course=course, name='Short-only load', academic_year='capacity')
            users = [roster[name] for name in fixture['usernames'][:count]]
            enrollments = CourseEnrollment.objects.bulk_create([CourseEnrollment(course=course, class_group=group, student=user) for user in users])
            CourseEnrollmentAccessPeriod.objects.bulk_create([CourseEnrollmentAccessPeriod(enrollment=row, course=course,
                class_group=group, student_id=row.student_id, revision=1, active_from=row.enrolled_at) for row in enrollments])
            CourseSubject.objects.bulk_create([CourseSubject(course=course, student=user) for user in users])
            bank = QuestionBank.objects.create(course=course, owner=owner, created_by=owner, name='Cloned baseline bank')
            concepts = {}
            cloned = []
            for index, source in enumerate(questions):
                spec = protocol_question_spec(index) if protocol else {'question_type': source.question_type,
                    'correct_answer': source.correct_answer, 'explanation': source.explanation}
                question = Question.objects.create(bank=bank, sequence=source.sequence, code=source.code,
                    text=source.text, teacher_difficulty=source.teacher_difficulty, **spec)
                allocate_course_question_identity(course, question)
                for link in source.concept_links.all():
                    if link.concept_id not in concepts:
                        concepts[link.concept_id] = Concept.objects.create(course=course, name=link.concept.name)
                    QuestionConcept.objects.create(question=question, concept=concepts[link.concept_id], is_primary=link.is_primary)
                if protocol:
                    if question.question_type in (Question.Type.SINGLE, Question.Type.MULTIPLE):
                        QuestionOption.objects.bulk_create([QuestionOption(question=question, key=key, text=key,
                            display_order=order) for order, key in enumerate(('A', 'B'), 1)])
                else:
                    QuestionOption.objects.bulk_create([QuestionOption(question=question, key=row.key, text=row.text,
                        display_order=row.display_order) for row in source.options.all()])
                cloned.append(question)
            experiment = Experiment.objects.create(course=course, owner=owner, created_by=owner, question_bank=bank,
                name=f'Short storm {count}', sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=20,
                feedback_mode=Experiment.Feedback.FULL if protocol else Experiment.Feedback.NONE,
                feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
                research_tools=protocol_tools([{'question_type': row.question_type, 'code': row.code} for row in cloned]) if protocol else {})
            ExperimentQuestion.objects.bulk_create([ExperimentQuestion(experiment=experiment, question=row, display_order=index)
                for index, row in enumerate(cloned, 1)])
            ExperimentSamplingKC.objects.bulk_create([ExperimentSamplingKC(experiment=experiment, concept=row) for row in concepts.values()])
            plan = get_or_create_publish_plan(experiment, owner)
            PublishPlanClass.objects.create(publish_plan=plan, class_group=group)
            generate_preview(plan, owner, expected_revision=plan.plan_revision)
            plan.refresh_from_db()
            task = commit_publish_plan(plan, owner, expected_revision=plan.plan_revision)[0]
            manifest = {'database': 'kt_e2e', 'run_id': run, 'student_count': count, 'item_count': 20,
                'task_id': task.pk, 'course_id': course.pk, 'experiment_id': experiment.pk, 'class_id': group.pk,
                'password': fixture['password'], 'usernames': fixture['usernames'][:count], 'source_config': config}
            if protocol:
                manifest.update(capture_protocol='school-capture-v2-protocol-v1', required_capture_v2_course_ids=[course.pk])
            path = output / f'short-{count}.json'
            with path.open('x') as handle:
                json.dump(manifest, handle)
            os.chmod(path, 0o600)
            paths.append(str(path))
        self.stdout.write(json.dumps({'student_counts': list(sizes), 'fixture_files': paths, 'formal_task_consumed': False}))
