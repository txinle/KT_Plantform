"""Publish a fresh baseline using an owned archived cohort, without altering its history."""
import hashlib
import json
import os
from pathlib import Path
import re

from django.conf import settings
from django.contrib.auth.hashers import check_password
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction

from apps.accounts.models import CourseEnrollment, User
from apps.experiments.models import (ClassExperimentTask, Experiment, ExperimentQuestion,
                                    ExperimentQuestionQuota, ExperimentSamplingKC)
from apps.experiments.publication_services import (commit_publish_plan, generate_preview,
    get_or_create_publish_plan, question_release_fingerprint, save_plan_selection)
from apps.assessment.confidence_services import confidence_scale_contract
from apps.assessment.models import ExperimentSession, PreparedAttemptProjection, PreparedSessionArtifact
from apps.assessment.prepared_content import canonical_json, read_display_content
from .seed_prepared_startup_load import Command as SeedCommand, SETTINGS_FIELDS, _private_stream, _sha


def require_isolated_database():
    database = str(connection.settings_dict['NAME'])
    if (connection.vendor != 'postgresql' or not (database.startswith('test_') or database.endswith('_e2e'))
            or os.getenv('SCHOOL_RUNTIME_ISOLATION_ENABLED') != '1'):
        raise CommandError('An explicitly isolated PostgreSQL test_* or *_e2e database is required')
    return database


def _snapshot_binding(snapshot):
    return {field.attname: getattr(snapshot, field.attname) for field in snapshot._meta.concrete_fields
            if field.attname not in ('id', 'version_id')}


def _projections(task_id):
    return list(PreparedAttemptProjection.objects.filter(attempt__session__class_task_id=task_id).select_related(
        'attempt__assignment__snapshot', 'attempt__confidence_assignment', 'attempt__session__version').order_by(
        'attempt__session__student_id', 'attempt__assignment__assigned_sequence_index'))


def _source_bindings(fixture):
    """Check archived identities and frozen content; current historical statuses may differ."""
    try:
        spec, ownership = fixture['fixture_spec'], fixture['source_ownership']
        accounts, tasks, run = fixture['accounts'], fixture['tasks'], fixture['run_id']
        if (fixture.get('isolated_demo') is not True or fixture['database'] != 'kt_e2e'
                or ownership['command'] != 'seed_prepared_startup_load' or _sha(spec) != fixture['plan_sha']
                or spec['source_sha256'] != ownership['source_sha256']
                or spec['database'] != fixture['database'] or spec['run_id'] != run
                or spec['tasks'] != tasks or spec['settings_binding'] != fixture['settings_binding']
                or fixture['student_count'] != 180 or fixture['item_count'] != 20
                or spec['student_count'] != 180 or spec['question_count'] != 20 or len(accounts) != 180 or len(tasks) != 1):
            raise CommandError('Source fixture ownership, specification or hash differs')
        if spec['account_bindings'] != [
                {'account_id': a['account_id'], 'user_id': a['user_id'], 'task_id': a['taskId'],
                 'session_id': a['session_id'], 'initial_state': a['initial_state']} for a in accounts]:
            raise CommandError('Source account bindings differ')
        if spec['content_binding_sha256'] != _sha([
                {'account_id': a['account_id'], 'manifest_sha256': a['manifest_sha256'], 'questions': a['questions']}
                for a in accounts]):
            raise CommandError('Source content bindings differ')
        task_spec = tasks[0]
        task = ClassExperimentTask.objects.select_related('experiment__owner', 'class_group__course',
            'release_snapshot').get(pk=task_spec['task_id'])
        experiment = task.experiment
        if (experiment.pk != task_spec['experiment_id'] or experiment.course_id != task_spec['course_id']
                or task.class_group_id != task_spec['class_id'] or task.class_group.course_id != experiment.course_id
                or ownership['teacher_id'] != experiment.owner_id or experiment.course.owner_id != experiment.owner_id
                or task.class_group.owner_id != experiment.owner_id
                or ownership['course_ids'] != [experiment.course_id] or ownership['class_ids'] != [task.class_group_id]
                or experiment.owner.username != f'pp-{run}-teacher'
                or experiment.owner.role != User.Role.TEACHER or not experiment.owner.is_active
                or experiment.course.code != f'PP-{run}-0'
                or experiment.frozen_release_snapshot_id != task.release_snapshot_id):
            raise CommandError('Source task/course/teacher ownership differs')
        for name in ('confidence_mode', 'confidence_scale_version'):
            if task_spec[name] != getattr(experiment, name):
                raise CommandError('Source confidence configuration differs')
        if task_spec['confidence_probability'] != str(experiment.confidence_probability):
            raise CommandError('Source confidence probability differs')
        students = {student.pk: student for student in User.objects.filter(pk__in=[a['user_id'] for a in accounts])}
        if len(students) != 180 or len({a['account_id'] for a in accounts}) != 180:
            raise CommandError('Source cohort identities are missing or duplicated')
        if set(CourseEnrollment.objects.filter(course_id=experiment.course_id, class_group_id=task.class_group_id
                ).values_list('student_id', flat=True)) != set(students):
            raise CommandError('Source enrollment ownership differs')
        sessions = {s.pk: s for s in ExperimentSession.objects.filter(class_task=task).select_related('participant_snapshot')}
        artifacts = {a.session_id: a for a in PreparedSessionArtifact.objects.filter(session__class_task=task)}
        projections = _projections(task.pk)
        questions_by_session = {}
        snapshots = {}
        for projection in projections:
            attempt, assignment = projection.attempt, projection.attempt.assignment
            snapshots[assignment.snapshot_id] = assignment.snapshot
            questions_by_session.setdefault(attempt.session_id, []).append({
                'attempt_id': attempt.pk, 'assignment_id': assignment.pk, 'snapshot_id': assignment.snapshot_id,
                'question_type': assignment.snapshot.question_type, 'sequence': assignment.assigned_sequence_index,
                'content_url': projection.content_descriptor['url'], 'content_sha256': projection.content_descriptor['sha256']})
        checked_passwords = set()
        source_sequence = None
        for index, account in enumerate(accounts):
            student = students[account['user_id']]
            session = sessions[account['session_id']]
            artifact = artifacts[session.pk]
            if (account['account_id'] != student.username or account['username'] != student.username
                    or student.username != f'pp-{run}-{index:03d}' or student.role != User.Role.STUDENT or not student.is_active
                    or account['taskId'] != task.pk or account['courseId'] != experiment.course_id
                    or session.student_id != student.pk or session.version_id != task.release_snapshot_id
                    or session.participant_snapshot.student_id != student.pk
                    or session.participant_snapshot.class_task_id != task.pk
                    or not artifact.ready or artifact.item_count != 20
                    or account['manifest_url'] != artifact.manifest_descriptor['url']
                    or account['manifest_sha256'] != artifact.manifest_descriptor['sha256']
                    or account['questions'] != questions_by_session[session.pk] or len(account['questions']) != 20):
                raise CommandError('Source student/session/manifest or all-20 content binding differs')
            credentials = (student.password, account['password'])
            if credentials not in checked_passwords:
                if not check_password(account['password'], student.password):
                    raise CommandError('Source account password differs')
                checked_passwords.add(credentials)
            sequence = [(q['snapshot_id'], q['sequence']) for q in account['questions']]
            if source_sequence is not None and sequence != source_sequence:
                raise CommandError('Source fixed question order differs between accounts')
            source_sequence = sequence
            read_display_content(artifact.manifest_descriptor)
        if len(snapshots) != 20 or {s.question_type for s in snapshots.values()} != {
                'single_choice', 'multiple_choice', 'numeric', 'short_answer', 'fill_blank'}:
            raise CommandError('Source must contain twenty questions across the five source types')
        for snapshot in snapshots.values():
            if (snapshot.source_question.bank.course_id != experiment.course_id
                    or snapshot.source_question.bank.owner_id != experiment.owner_id
                    or not snapshot.release_fingerprint
                    or snapshot.release_fingerprint != question_release_fingerprint(snapshot.source_question)):
                raise CommandError('Source question content/scoring differs from its frozen snapshot')
        for projection in projections[:20]:
            read_display_content(projection.content_descriptor)
        if not check_password(accounts[0]['password'], experiment.owner.password):
            raise CommandError('Source teacher credentials differ from the owned seed fixture')
        return task, [students[a['user_id']] for a in accounts], snapshots
    except (KeyError, TypeError, ValueError, AttributeError, ClassExperimentTask.DoesNotExist, OSError) as error:
        raise CommandError(f'Source fixture bindings cannot be verified: {error}') from error


class Command(BaseCommand):
    help = 'Create one reusable prepared capacity baseline from an archived owned 180-account/20-question fixture.'

    def add_arguments(self, parser):
        parser.add_argument('--source-fixture', required=True)
        parser.add_argument('--baseline-id', required=True)
        parser.add_argument('--run-id', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        database = require_isolated_database()
        if not settings.ASSESSMENT_PREPARED_ENABLED:
            raise CommandError('Prepared publication must be enabled')
        for key in ('baseline_id', 'run_id'):
            if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,31}', options[key]):
                raise CommandError(f'{key} must contain 1..32 letters, digits or hyphens')
        output = Path(options['output']).resolve()
        if output.exists():
            raise CommandError('Output already exists; refusing overwrite')
        source_path = Path(options['source_fixture']).resolve()
        source_bytes = source_path.read_bytes()
        try:
            source = json.loads(source_bytes)
        except (ValueError, UnicodeError) as error:
            raise CommandError('Source fixture is not valid JSON') from error
        with _private_stream(output) as stream:
            try:
                with transaction.atomic():
                    with connection.cursor() as cursor:
                        cursor.execute('SELECT pg_advisory_xact_lock(%s)', [int.from_bytes(hashlib.sha256(
                            f"capacity-baseline:{options['baseline_id']}".encode()).digest()[:8], 'big', signed=True)])
                    marker = f"capacity-baseline:{options['baseline_id']}"
                    if Experiment.objects.filter(description=marker).exists():
                        raise CommandError('Baseline namespace already exists; restore the frozen baseline instead')
                    task, students, snapshots = _source_bindings(source)
                    fixture = self._publish(source, task, students, snapshots, database, options, marker)
                    fixture['source_fixture_sha256'] = hashlib.sha256(source_bytes).hexdigest()
                    json.dump(fixture, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            except Exception:
                stream.close()
                output.unlink()
                raise
        self.stdout.write(json.dumps({'fixture': str(output), 'fixture_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
            'source_fixture_id': fixture['source_fixture_id'], 'baseline_snapshot_id': options['baseline_id'],
            'fixture_id': fixture['fixture_id'], 'student_count': 180, 'question_count': 20, 'database': database}))

    def _publish(self, source, source_task, students, source_snapshots, database, options, marker):
        original = source_task.experiment
        fields = {name: getattr(original, name) for name in Experiment.FROZEN_DEFINITION_FIELDS
                  if name not in ('name', 'description', 'frozen_release_snapshot_id')}
        experiment = Experiment.objects.create(**fields, owner=original.owner, created_by=original.owner,
            name=f"Capacity baseline {options['baseline_id']}", description=marker)
        if list(experiment.question_bank_links.values('question_bank_id', 'selection_order')) != list(
                original.question_bank_links.values('question_bank_id', 'selection_order')):
            raise CommandError('Source question-bank selection differs from its owned single-bank fixture')
        for model, names in ((ExperimentQuestion, ('question_id', 'display_order')),
                (ExperimentSamplingKC, ('concept_id', 'quota', 'match_scope'))):
            model.objects.bulk_create([model(experiment=experiment, **row)
                for row in model.objects.filter(experiment=original).values(*names)])
        for quota in original.question_quotas.all():
            ExperimentQuestionQuota.objects.filter(experiment=experiment, question_type=quota.question_type).update(amount=quota.amount)
        plan = get_or_create_publish_plan(experiment, original.owner)
        plan = save_plan_selection(plan, original.owner, expected_revision=plan.plan_revision,
                                   class_ids=[source_task.class_group_id], exclusions_by_class={})
        preview = generate_preview(plan, original.owner, expected_revision=plan.plan_revision)
        plan.refresh_from_db()
        task = commit_publish_plan(plan, original.owner, expected_revision=plan.plan_revision)[0]
        if task.release_snapshot.configuration != source_task.release_snapshot.configuration:
            raise CommandError('Published baseline configuration differs from the source frozen configuration')
        old_by_question = {s.source_question_id: _snapshot_binding(s) for s in source_snapshots.values()}
        new_snapshots = list(task.release_snapshot.question_snapshots.all())
        if old_by_question != {s.source_question_id: _snapshot_binding(s) for s in new_snapshots}:
            raise CommandError('Published baseline content/scoring/KC snapshot differs from source')
        tasks = [{'task_id': task.pk, 'experiment_id': experiment.pk, 'preview_id': preview.pk,
            'release_snapshot_id': task.release_snapshot_id, 'randomization_identity_id': task.randomization_identity_id,
            'course_id': original.course_id, 'class_id': source_task.class_group_id,
            'confidence_mode': experiment.confidence_mode, 'confidence_probability': str(experiment.confidence_probability),
            'confidence_scale_version': experiment.confidence_scale_version}]
        layout = [{'index': index, 'task_index': 0, 'active': False} for index in range(180)]
        accounts = SeedCommand()._export_accounts(options['run_id'], layout, tasks, students)
        projections = {p.attempt_id: p for p in _projections(task.pk)}
        mappings = []
        for old, new in zip(source['accounts'], accounts, strict=True):
            new['password'] = old['password']
            new['manifest_descriptor'] = PreparedSessionArtifact.objects.get(session_id=new['session_id']).manifest_descriptor
            read_display_content(new['manifest_descriptor'])
            question_mapping = []
            for old_q, new_q in zip(old['questions'], new['questions'], strict=True):
                projection = projections[new_q['attempt_id']]
                attempt, assignment = projection.attempt, projection.attempt.assignment
                confidence = attempt.confidence_assignment
                snapshot = assignment.snapshot
                if (snapshot.source_question_id != source_snapshots[old_q['snapshot_id']].source_question_id
                        or old_q['sequence'] != new_q['sequence'] or attempt.session.status != 'prepared'
                        or attempt.session.started_at is not None or attempt.status != 'unvisited'
                        or attempt.current_response_snapshot_id is not None or attempt.final_response_snapshot_id is not None):
                    raise CommandError('New baseline is not a fresh prepared copy of the assigned source paper')
                new_q.update(source_question_id=snapshot.source_question_id, content_descriptor=projection.content_descriptor,
                    confidence_assignment_id=str(confidence.pk), confidence_condition=confidence.condition,
                    confidence_required=confidence.condition == 'prompt',
                    confidence_sampling_probability=str(experiment.confidence_probability),
                    confidence_scale_version=experiment.confidence_scale_version,
                    confidence_scale=confidence_scale_contract(attempt),
                    pending_confidence=projection.runtime['pending_confidence'],
                    confidence_assignment_version=confidence.assignment_version,
                    confidence_sampling_unit=confidence.assignment_unit,
                    snapshot_sha256=hashlib.sha256(canonical_json(_snapshot_binding(snapshot))).hexdigest())
                question_mapping.append({'source_question_id': snapshot.source_question_id,
                    'old_assignment_id': old_q['assignment_id'], 'new_assignment_id': new_q['assignment_id'],
                    'old_attempt_id': old_q['attempt_id'], 'new_attempt_id': new_q['attempt_id'],
                    'old_snapshot_id': old_q['snapshot_id'], 'new_snapshot_id': new_q['snapshot_id']})
            mappings.append({'account_id': old['account_id'], 'user_id': old['user_id'],
                'old_task_id': old['taskId'], 'new_task_id': task.pk, 'old_session_id': old['session_id'],
                'new_session_id': new['session_id'], 'questions': question_mapping})
        settings_binding = {name: getattr(settings, name) for name in SETTINGS_FIELDS}
        spec = {'version': 'student-capacity-baseline-v1', 'database': database, 'run_id': options['run_id'],
            'source_fixture_id': source['plan_sha'], 'baseline_snapshot_id': options['baseline_id'],
            'scenario': 'same-task', 'student_count': 180, 'question_count': 20, 'tasks': tasks,
            'settings_binding': settings_binding, 'account_mapping': mappings,
            'release_configuration': task.release_snapshot.configuration,
            'content_binding_sha256': _sha(accounts)}
        fixture_id = _sha(spec)
        return {'isolated_demo': True, 'database': database, 'run_id': options['run_id'], 'scenario': 'same-task',
            'source_fixture_id': source['plan_sha'], 'baseline_snapshot_id': options['baseline_id'],
            'fixture_id': fixture_id, 'plan_sha': fixture_id, 'fixture_spec': spec, 'tasks': tasks, 'accounts': accounts,
            'student_count': 180, 'item_count': 20, 'settings_binding': settings_binding, 'account_mapping': mappings,
            'teacher_account': {'user_id': original.owner_id, 'username': original.owner.username,
                                'password': source['accounts'][0]['password']},
            'source_ownership': {'command': 'prepare_student_capacity_baseline', 'teacher_id': original.owner_id,
                'course_ids': [original.course_id], 'class_ids': [source_task.class_group_id],
                'source_sha256': source['source_ownership']['source_sha256']},
            'evidence_scope': 'Reusable publication baseline only; histories retained, no capacity load executed.'}
