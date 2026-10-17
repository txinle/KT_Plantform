"""Fresh, isolated prepared-startup cohorts; never reuse production rosters."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import uuid
from decimal import Decimal

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import (ClassGroup, Course, CourseEnrollment, CourseEnrollmentAccessPeriod,
                                  CourseSubject, ResearchConsent, User)
from apps.accounts.research_consent import CURRENT_RESEARCH_CONSENT_VERSION
from apps.experiments.models import Experiment, ExperimentQuestion, ExperimentSamplingKC
from apps.experiments.publication_services import (get_or_create_publish_plan, save_plan_selection,
                                                  generate_preview, commit_publish_plan)
from apps.question_bank.models import Concept, Question, QuestionBank, QuestionConcept, QuestionOption
from apps.question_bank.services.course_question_identity import allocate_course_question_identity
from apps.assessment.models import PreparedAttemptProjection, PreparedSessionArtifact
from apps.assessment.prepared_services import activate_prepared_session

TEST_PASSWORD = 'PreparedFixture!test-only'
SCENARIOS = ('same-task', 'three-task', 'mixed')
SETTINGS_FIELDS = ('ASSESSMENT_PREPARED_ENABLED', 'ASSESSMENT_ADMISSION_ENABLED',
                   'SCHOOL_POOL_PROFILE', 'ASSESSMENT_ENTRY_WORKERS', 'ASSESSMENT_ENTRY_DB_CONNECTIONS',
                   'ASSESSMENT_ENTRY_LOGIN_LIMIT', 'ASSESSMENT_ENTRY_STARTUP_LIMIT', 'ASSESSMENT_ENTRY_HEAVY_LIMIT',
                   'ASSESSMENT_ADMISSION_GLOBAL_ENABLED', 'ASSESSMENT_ADMISSION_CAPACITY',
                   'ASSESSMENT_ADMISSION_RATE_PER_SECOND', 'ASSESSMENT_ADMISSION_LEASE_SECONDS',
                   'ASSESSMENT_STARTUP_WORKERS', 'ASSESSMENT_REMAINING_WORKERS',
                   'ASSESSMENT_STARTUP_DB_CONNECTIONS', 'ASSESSMENT_REMAINING_DB_CONNECTIONS',
                   'ASSESSMENT_ACTIVATION_STATEMENT_TIMEOUT_MS', 'ASSESSMENT_ACTIVATION_LOCK_TIMEOUT_MS')


def fixture_layout(scenario, *, students=180):
    if scenario not in SCENARIOS:
        raise CommandError('Unknown prepared startup scenario')
    if students not in (180, 240, 360):
        raise CommandError('Student count must be 180, 240 or 360')
    return [{'index': n, 'task_index': n // (students // 3) if scenario == 'three-task' else 0,
             'active': scenario == 'mixed' and n < students // 3} for n in range(students)]


def _sha(document):
    return hashlib.sha256(json.dumps(document, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=False).encode()).hexdigest()


def _source_hashes():
    sources = ('server/apps/assessment/management/commands/seed_prepared_startup_load.py',
               'server/apps/assessment/prepared_services.py', 'server/apps/assessment/prepared_content.py',
               'server/apps/experiments/publication_services.py', 'server/apps/assessment/admission.py')
    return {source: hashlib.sha256((settings.BASE_DIR / Path(source).relative_to('server')).read_bytes()).hexdigest()
            for source in sources}


def _question_spec(index):
    kind = (Question.Type.SINGLE, Question.Type.MULTIPLE, Question.Type.NUMERIC,
            Question.Type.SHORT, Question.Type.FILL_BLANK)[index % 5]
    answer = {Question.Type.SINGLE: 'A', Question.Type.MULTIPLE: ['A', 'B'],
              Question.Type.NUMERIC: '4', Question.Type.SHORT: '用已知条件解释推理过程。',
              Question.Type.FILL_BLANK: '四'}[kind]
    return {'question_type': kind, 'correct_answer': answer,
            'explanation': '隔离 fixture 参考答案，不属于学生可见预备内容。'}


def _private_stream(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    stream = path.open('x', encoding='utf-8')
    try:
        os.chmod(path, 0o600)
        if os.name == 'nt':
            identity = f"{os.environ['USERDOMAIN']}\\{os.environ['USERNAME']}"
            subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'{identity}:(F)'],
                           check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return stream
    except Exception:
        stream.close()
        path.unlink()
        raise


class Command(BaseCommand):
    help = 'Create one fresh isolated 180-person prepared startup cohort and a private runner account manifest.'

    def add_arguments(self, parser):
        parser.add_argument('--run-id', required=True)
        parser.add_argument('--scenario', choices=SCENARIOS, required=True)
        parser.add_argument('--questions', type=int, choices=(20, 200), default=20)
        parser.add_argument('--output', required=True)
        parser.add_argument('--students', type=int, choices=(180, 240, 360), default=180)

    def handle(self, *args, **options):
        database = str(connection.settings_dict['NAME'])
        if connection.vendor != 'postgresql' or not (database.startswith('test_') or database.endswith('_e2e')):
            raise CommandError('Only explicit isolated PostgreSQL databases test_* or *_e2e are permitted')
        if not all(getattr(settings, name, False) for name in (
                'ASSESSMENT_PREPARED_ENABLED', 'ASSESSMENT_ADMISSION_ENABLED', 'ASSESSMENT_ADMISSION_GLOBAL_ENABLED')):
            raise CommandError('Prepared and experimental global admission must be explicitly configured')
        startup_budgets = (settings.ASSESSMENT_ENTRY_WORKERS, settings.ASSESSMENT_ENTRY_DB_CONNECTIONS,
                           settings.ASSESSMENT_ENTRY_STARTUP_LIMIT) if settings.SCHOOL_POOL_PROFILE in ('shared-entry-v1', 'shared-entry-v2') else (
                           settings.ASSESSMENT_STARTUP_WORKERS, settings.ASSESSMENT_STARTUP_DB_CONNECTIONS)
        if not 1 <= settings.ASSESSMENT_ADMISSION_CAPACITY <= min(startup_budgets):
            raise CommandError('Global C must fit the configured startup worker and database budgets')
        run = options['run_id']
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,31}', run):
            raise CommandError('Use a unique run-id of 1..32 letters/digits/hyphens')
        if options['questions'] not in (20, 200):
            raise CommandError('Question count must be 20 or 200')
        layout = fixture_layout(options['scenario'], students=options.get('students', 180))
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Output already exists; refusing overwrite or fixture reuse')
        if User.objects.filter(username__startswith=f'pp-{run}-').exists() or Course.objects.filter(code__startswith=f'PP-{run}-').exists():
            raise CommandError('Run namespace already exists; use a fresh run-id')
        with _private_stream(output) as stream:
            try:
                with transaction.atomic():
                    fixture = self._seed(run, layout, options['scenario'], options['questions'], database)
                json.dump(fixture, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            except Exception:
                stream.close()
                output.unlink()
                raise
        self.stdout.write(json.dumps({'fixture': str(output), 'fixture_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
                                     'run_id': run, 'scenario': options['scenario'], 'student_count': len(layout),
                                     'question_count': options['questions'], 'database': database}))

    def _seed(self, run, layout, scenario, count, database):
        password_hash = make_password(TEST_PASSWORD)
        owner = User.objects.create(username=f'pp-{run}-teacher', password=password_hash, role=User.Role.TEACHER)
        students = User.objects.bulk_create([
            User(username=f'pp-{run}-{n:03d}', student_number=f'pp-{run}-{n:03d}',
                 display_name=f'隔离学生{n:03d}', password=password_hash, role=User.Role.STUDENT)
            for n in range(len(layout))])
        ResearchConsent.objects.bulk_create([ResearchConsent(student=student, version=CURRENT_RESEARCH_CONSENT_VERSION)
                                            for student in students])
        tasks, groups, courses = [], [], []
        for task_index in sorted({row['task_index'] for row in layout}):
            course = Course.objects.create(owner=owner, name=f'Prepared startup {run} {task_index}',
                                           code=f'PP-{run}-{task_index}', term='isolated-fixture')
            group = ClassGroup.objects.create(course=course, owner=owner, name='隔离预备压测班', academic_year='fixture')
            roster = [students[row['index']] for row in layout if row['task_index'] == task_index]
            enrollments = CourseEnrollment.objects.bulk_create([
                CourseEnrollment(course=course, class_group=group, student=student) for student in roster])
            CourseEnrollmentAccessPeriod.objects.bulk_create([
                CourseEnrollmentAccessPeriod(enrollment=enrollment, course=course, class_group=group,
                                             student_id=enrollment.student_id, revision=1, active_from=enrollment.enrolled_at)
                for enrollment in enrollments])
            CourseSubject.objects.bulk_create([CourseSubject(course=course, student=student) for student in roster])
            bank = QuestionBank.objects.create(course=course, owner=owner, created_by=owner, name='隔离代表题库')
            concept = Concept.objects.create(course=course, name='隔离推理知识点')
            questions = []
            for index in range(count):
                question = Question.objects.create(bank=bank, sequence=index + 1, code=f'PP-Q{index + 1:03d}',
                                                   text=f'代表题目 {index + 1}：根据给定条件作答。', **_question_spec(index))
                allocate_course_question_identity(course, question)
                QuestionConcept.objects.create(question=question, concept=concept, is_primary=True)
                questions.append(question)
            QuestionOption.objects.bulk_create([
                QuestionOption(question=question, key=key, text=f'选项 {key}', display_order=order)
                for question in questions if question.question_type in (Question.Type.SINGLE, Question.Type.MULTIPLE)
                for order, key in enumerate(('A', 'B', 'C'), 1)])
            experiment = Experiment.objects.create(
                course=course, owner=owner, created_by=owner, question_bank=bank, name=f'预备压测 {run} {task_index}',
                sampling_mode=Experiment.SamplingMode.TOTAL_COUNT, sampling_total_count=count,
                ordering_mode=Experiment.Ordering.FIXED, feedback_mode=Experiment.Feedback.NONE,
                feedback_release_policy=Experiment.FeedbackReleasePolicy.AFTER_PERSONAL_SUBMISSION,
                confidence_mode=Experiment.ConfidenceMode.SAMPLED, confidence_sampling_unit=Experiment.ConfidenceSamplingUnit.ATTEMPT,
                confidence_probability=Decimal('0.500'), confidence_scale_version='v2', confidence_assignment_version='v2',
                confidence_randomization_seed=f'fixture-{run}-{task_index}', research_tools={})
            ExperimentQuestion.objects.bulk_create([ExperimentQuestion(experiment=experiment, question=question, display_order=n)
                                                     for n, question in enumerate(questions, 1)])
            ExperimentSamplingKC.objects.create(experiment=experiment, concept=concept)
            plan = get_or_create_publish_plan(experiment, owner)
            plan = save_plan_selection(plan, owner, expected_revision=plan.plan_revision,
                                       class_ids=[group.pk], exclusions_by_class={})
            preview = generate_preview(plan, owner, expected_revision=plan.plan_revision)
            plan.refresh_from_db()
            task = commit_publish_plan(plan, owner, expected_revision=plan.plan_revision)[0]
            tasks.append({'task_id': task.pk, 'experiment_id': experiment.pk, 'preview_id': preview.pk,
                          'course_id': course.pk, 'class_id': group.pk, 'confidence_mode': 'sampled',
                          'confidence_probability': '0.500', 'confidence_scale_version': 'v2'})
            courses.append(course.pk)
            groups.append(group.pk)
        # Autovacuum cannot see this transaction's fresh cohort and may have
        # analyzed the empty test tables. Refresh only these fixture relations
        # before the joined export so its row estimates reflect the new rows.
        with connection.cursor() as cursor:
            for table in ('assessment_experimentsession', 'assessment_attempt',
                          'assessment_studentquestionassignment', 'assessment_preparedattemptprojection',
                          'experiments_questionsnapshot'):
                cursor.execute(f'ANALYZE {connection.ops.quote_name(table)}')
        accounts = self._export_accounts(run, layout, tasks, students)
        settings_binding = {name: getattr(settings, name) for name in SETTINGS_FIELDS}
        fixture_spec = {'version': 'prepared-startup-fixture-v1', 'database': database, 'run_id': run,
                        'scenario': scenario, 'student_count': len(layout), 'question_count': count,
                        'settings_binding': settings_binding, 'tasks': tasks,
                        'account_bindings': [{'account_id': a['account_id'], 'user_id': a['user_id'],
                                             'task_id': a['taskId'], 'session_id': a['session_id'],
                                             'initial_state': a['initial_state']} for a in accounts]}
        source_hashes = _source_hashes()
        fixture_spec['source_sha256'] = source_hashes
        fixture_spec['content_binding_sha256'] = _sha([
            {'account_id': a['account_id'], 'manifest_sha256': a['manifest_sha256'], 'questions': a['questions']}
            for a in accounts])
        return {'isolated_demo': True, 'database': database, 'run_id': run, 'scenario': scenario,
                'student_count': len(layout), 'item_count': count, 'accounts': accounts, 'tasks': tasks,
                'settings_binding': settings_binding, 'fixture_spec': fixture_spec, 'plan_sha': _sha(fixture_spec),
                'source_ownership': {'command': 'seed_prepared_startup_load', 'teacher_id': owner.pk,
                                     'course_ids': courses, 'class_ids': groups, 'source_sha256': source_hashes},
                'evidence_scope': 'Fixture specification hash only; freeze the runner plan after aggregating fresh cohorts. No load was executed.'}

    def _export_accounts(self, run, layout, tasks, students):
        task_ids = [task['task_id'] for task in tasks]
        artifacts = {row['session__student_id']: row for row in PreparedSessionArtifact.objects.filter(
            session__class_task_id__in=task_ids).values('session__student_id', 'session_id', 'ready', 'manifest_descriptor')}
        questions_by_session = {}
        for p in PreparedAttemptProjection.objects.filter(attempt__session__class_task_id__in=task_ids).order_by(
                'attempt__assignment__assigned_sequence_index').values(
                'attempt__session_id', 'attempt_id', 'attempt__assignment_id', 'attempt__assignment__snapshot_id',
                'attempt__assignment__snapshot__question_type', 'attempt__assignment__assigned_sequence_index', 'content_descriptor'):
            questions_by_session.setdefault(p['attempt__session_id'], []).append({
                'attempt_id': p['attempt_id'], 'assignment_id': p['attempt__assignment_id'],
                'snapshot_id': p['attempt__assignment__snapshot_id'],
                'question_type': p['attempt__assignment__snapshot__question_type'],
                'sequence': p['attempt__assignment__assigned_sequence_index'],
                'content_url': p['content_descriptor']['url'], 'content_sha256': p['content_descriptor']['sha256']})
        accounts = []
        for row in layout:
            student, task = students[row['index']], tasks[row['task_index']]
            artifact = artifacts.get(student.pk)
            if not artifact or not artifact['ready']:
                raise CommandError('Actual publication did not produce a ready prepared artifact')
            receipt = None
            if row['active']:
                stable = uuid.uuid5(uuid.NAMESPACE_URL, f'prepared-fixture:{run}:{student.pk}')
                _, receipt = activate_prepared_session(student=student, task_id=task['task_id'],
                                                       client_instance_id=stable, navigation_id=stable)
            questions = questions_by_session[artifact['session_id']]
            manifest = artifact['manifest_descriptor']
            accounts.append({'account_id': student.username, 'user_id': student.pk, 'username': student.username,
                             'password': TEST_PASSWORD, 'courseId': task['course_id'], 'taskId': task['task_id'],
                             'session_id': artifact['session_id'], 'initial_state': 'active' if row['active'] else 'prepared',
                             'question_url': receipt['question_url'] if receipt else None,
                             'save_value': 'A' if receipt else None, 'questions': questions,
                             'manifest_url': manifest['url'], 'manifest_sha256': manifest['sha256'],
                             'warm_urls': [manifest['url'], questions[0]['content_url']]})
        return accounts
