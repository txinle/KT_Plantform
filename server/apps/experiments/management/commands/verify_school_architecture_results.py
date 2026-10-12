"""Isolated, rollback-only reconciliation of grading, Coverage and export facts."""

import csv
import hashlib
import io
import json
import os
import zipfile
from collections import Counter
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction
from django.db.models import F

from apps.assessment.feedback_services import initial_grading_result
from apps.assessment.models import Attempt, ExperimentSession, GradeRecord
from apps.assessment.response_snapshot_services import canonical_response_state
from apps.analytics.models import ResearchSessionBehavior, ResearchSessionMaterialization, ResearchSessionResponse
from apps.analytics.recording_coverage import generate_scope_coverage
from apps.analytics.research_contract import FILES
from apps.analytics.research_dataset import read_research_bundle
from apps.analytics.research_export_v2 import _write_fact_csv
from apps.analytics.research_materialization import PROJECTION_VERSION, _scope_for_session
from apps.experiments.models import ClassExperimentTask
from apps.question_bank.services.scoring import score_payload
from scripts.load.capture_protocol import CONTRACT, digest


def require_isolated_database():
    if (os.environ.get('POSTGRES_DB') != 'kt_e2e'
            or connection.settings_dict['NAME'] != 'kt_e2e'
            or connection.vendor != 'postgresql'):
        raise CommandError('Only explicit direct isolated PostgreSQL kt_e2e is permitted')


def require_binding(fixture, ledger):
    if (fixture.get('database') != 'kt_e2e' or not fixture.get('run_id')
            or fixture.get('capture_protocol') != CONTRACT
            or ledger.get('capture_protocol') != CONTRACT
            or ledger.get('task_id') != fixture.get('task_id')
            or ledger.get('source_config') != fixture.get('source_config')
            or not fixture.get('source_config')
            or ledger.get('student_count') != fixture.get('student_count')
            or not isinstance(ledger.get('sessions'), dict)
            or not fixture.get('usernames')
            or len(set(fixture['usernames'])) != fixture.get('student_count')
            or len(fixture['usernames']) != fixture.get('student_count')
            or not isinstance(fixture.get('item_count'), int) or fixture['item_count'] < 1):
        raise CommandError('Dedicated fixture/ledger binding required')
    task = ClassExperimentTask.objects.filter(pk=fixture['task_id']).first()
    if (task is None or task.experiment_id != fixture.get('experiment_id')
            or task.experiment.course_id != fixture.get('course_id')
            or task.class_group_id != fixture.get('class_id')):
        raise CommandError('Fixture task/course/class binding required')
    sessions = list(ExperimentSession.objects.filter(class_task=task).select_related(
        'student', 'capture_contract', 'version__experiment__course', 'class_task__release_snapshot'))
    if any(row.student.username not in fixture['usernames'] for row in sessions):
        raise CommandError('Task contains sessions outside fixture student binding')
    session_map = {str(row.pk): row for row in sessions}
    if any(key not in session_map or values.get('username_sha256') != digest(session_map[key].student.username)
           for key, values in ledger['sessions'].items()):
        raise CommandError('Ledger session/student binding required')
    seen_answers = set()
    for session_id, values in ledger['sessions'].items():
        owned = set(Attempt.objects.filter(session_id=session_id).values_list('pk', flat=True))
        answer_ids = {int(key) for key in values.get('answers', {})}
        if not answer_ids.issubset(owned) or seen_answers.intersection(answer_ids):
            raise CommandError('Ledger answer/session binding required')
        seen_answers.update(answer_ids)
    return sessions


def grade_checks(attempts):
    checks = {'automatic_grades_match_frozen_scoring': True, 'grade_projections_match_effective_facts': True,
              'pending_grades_are_not_zero_or_correctness': True}
    pending = automatic = manual = 0
    grade_ready = {}
    for attempt in attempts:
        submission = getattr(attempt, 'submission_event', None)
        if submission is None:
            checks['grade_projections_match_effective_facts'] = False
            grade_ready[attempt.pk] = False
            continue
        snapshot = attempt.assignment.snapshot
        correct, score = score_payload(snapshot.question_type, snapshot.correct_answer, submission.final_answer,
            accepted_answers=snapshot.accepted_answers, absolute_tolerance=snapshot.absolute_tolerance,
            relative_tolerance=snapshot.relative_tolerance)
        expected = initial_grading_result(attempt, correct, score)
        effective = list(submission.grade_records.filter(status=GradeRecord.Status.COMPLETED,
            validity_status=GradeRecord.ValidityStatus.EFFECTIVE))
        grade_ready[attempt.pk] = len(effective) == 1
        if not effective and expected.status == 'pending':
            pending += 1
            checks['pending_grades_are_not_zero_or_correctness'] &= (
                submission.grading_status == 'pending' and submission.score is None and submission.is_correct is None
                and attempt.score is None and attempt.is_correct is None
                and submission.grade_records.filter(status='pending', validity_status='candidate').exists())
            continue
        if len(effective) != 1:
            checks['grade_projections_match_effective_facts'] = False
            continue
        grade = effective[0]
        checks['grade_projections_match_effective_facts'] &= (
            submission.grading_status == 'completed' and grade.graded_at is not None
            and grade.score is not None and grade.max_score is not None and grade.is_correct is not None
            and 0 <= grade.score <= grade.max_score
            and submission.score == attempt.score == grade.score
            and submission.is_correct == attempt.is_correct == grade.is_correct)
        if expected.method == 'automatic':
            automatic += 1
            checks['automatic_grades_match_frozen_scoring'] &= (
                grade.grading_method == 'automatic' and grade.score == expected.score
                and grade.is_correct == expected.is_correct and grade.max_score == expected.max_score)
        else:
            manual += 1
            checks['grade_projections_match_effective_facts'] &= grade.grading_method == expected.method
    return checks, grade_ready, {'pending_grade_count': pending, 'automatic_grade_count': automatic,
                                'manual_grade_count': manual, 'graded_count': automatic + manual}


def memory_export_checks(sessions, materializations):
    """Use the production v2 fact writer; no jobs, assets or private files persist."""
    ids = [row.pk for row in materializations]
    expected = {'responses': [], 'behavior_events': []}
    for session in sorted(sessions, key=lambda row: row.pk):
        bundle, _ = read_research_bundle(_scope_for_session(session), include_course_catalog=False)
        for key in expected:
            expected[key].extend(bundle['files'][key])
    contracts = {item['key']: item for item in FILES}
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for key, model in (('responses', ResearchSessionResponse), ('behavior_events', ResearchSessionBehavior)):
            _write_fact_csv(archive, contract=contracts[key], model=model, materialization_ids=ids, identities={})
    counts = {}
    matched = True
    with zipfile.ZipFile(io.BytesIO(output.getvalue())) as archive:
        for key, expected_rows in expected.items():
            rows = list(csv.reader(io.StringIO(archive.read(contracts[key]['path']).decode('utf-8-sig'))))
            # csv.writer's scalar conversion is the actual export contract (None -> empty).
            reference = io.StringIO(newline='')
            writer = csv.writer(reference)
            writer.writerow(contracts[key]['headers'])
            writer.writerows(expected_rows)
            matched &= rows == list(csv.reader(io.StringIO(reference.getvalue())))
            counts[f'export_{"response" if key == "responses" else "behavior"}_count'] = max(0, len(rows) - 1)
    return matched, counts


class Command(BaseCommand):
    help = 'Reconcile fixture grading, Coverage and v2 export fact tables in kt_e2e; never repair source facts.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--output', required=True)

    def handle(self, *args, **options):
        require_isolated_database()
        try:
            path = Path(options['manifest'])
            fixture = json.loads(path.read_text(encoding='utf-8-sig'))
            ledger = json.loads(path.with_suffix('.ledger.json').read_text(encoding='utf-8'))
            claim = json.loads((path.parent / f'.school-task-{fixture["task_id"]}.claim.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, KeyError, TypeError):
            raise CommandError('Fixture/ledger/claim could not be read') from None
        if (claim.get('task_id') != fixture.get('task_id') or claim.get('run_id') != fixture.get('run_id')
                or claim.get('manifest_sha256') != hashlib.sha256(path.read_bytes()).hexdigest()):
            raise CommandError('Reserved workload manifest binding required')
        result = self.verify(fixture, ledger)
        with Path(options['output']).open('x', encoding='utf-8') as output:
            os.chmod(output.name, 0o600)
            json.dump(result, output, indent=2)
        self.stdout.write(json.dumps(result))
        if not result['passed']:
            raise CommandError(f'Architecture results verification {result["status"]}')

    def verify(self, fixture, ledger):
        result = {'database': 'kt_e2e', 'verification_mode': 'rollback_only_derived_coverage', 'persistent_writes': False,
                  'derived_coverage_rolled_back': True, 'export_scope': 'fixture_response_and_behavior_fact_tables',
                  'evidence_kind': 'grading_coverage_export_reconciliation', 'checks': {}, 'passed': False,
                  'raw_fact_integrity_passed': None, 'raw_fact_integrity_failed': False}
        try:
            with transaction.atomic():
                # A stable source view; tests may already own an outer transaction.
                if not connection.savepoint_ids:
                    with connection.cursor() as cursor:
                        cursor.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
                sessions = require_binding(fixture, ledger)
                checks = result['checks']
                checks['ledger_complete'] = ledger.get('incomplete') is False and all(
                    row.get('complete') is True for row in ledger['sessions'].values())
                checks['all_fixture_sessions_terminal'] = (len(sessions) == fixture['student_count']
                    and {str(row.pk) for row in sessions} == set(ledger['sessions'])
                    and {row.student.username for row in sessions} == set(fixture['usernames'])
                    and all(row.status == 'completed' and row.terminal_at is not None for row in sessions))
                result['session_count'] = len(sessions)
                if not all(checks.values()):
                    result['status'] = 'not_ready'
                    return result
                attempts = list(Attempt.objects.filter(session__in=sessions).select_related(
                    'session__capture_contract', 'session__class_task__release_snapshot', 'assignment__snapshot',
                    'submission_event__final_response_snapshot', 'final_response_snapshot'))
                answers = {int(key): value for row in ledger['sessions'].values() for key, value in row.get('answers', {}).items()}
                checks['ledger_answers_match_authoritative_submissions'] = (
                    len(attempts) == len(answers) == fixture['student_count'] * fixture['item_count']
                    and all((value := answers.get(row.pk)) is not None and row.status == 'submitted'
                        and row.submitted_at is not None and (submission := getattr(row, 'submission_event', None)) is not None
                        and submission.committed_at is not None and row.final_answer == submission.final_answer
                        and str(row.final_response_event_id) == str(row.active_response_event_id) == value['target_event_id']
                        and str(submission.submission_token) == value['submission_token']
                        and row.response_version == value['response_version']
                        and row.assignment.snapshot.question_type == value['question_type']
                        and digest({'question_type': value['question_type'], 'value': canonical_response_state(
                            value['question_type'], submission.final_answer)}) == value['state_sha256'] for row in attempts))
                result['attempt_count'] = len(attempts)
                if not checks['ledger_answers_match_authoritative_submissions']:
                    result.update(status='raw_fact_mismatch', raw_fact_integrity_passed=False, raw_fact_integrity_failed=True)
                    return result
                grades, grade_ready, counts = grade_checks(attempts)
                checks.update(grades)
                result.update(counts)
                materializations = list(ResearchSessionMaterialization.objects.filter(session__in=sessions,
                    status='succeeded', is_current=True, projection_version=PROJECTION_VERSION,
                    source_revision=F('session__research_source_revision')))
                checks['current_materializations_ready'] = len(materializations) == len(sessions)
                if not checks['current_materializations_ready']:
                    result['status'] = 'not_ready'
                    return result
                checks['export_rows_match_authoritative_facts'], counts = memory_export_checks(sessions, materializations)
                result.update(counts)
                run = generate_scope_coverage(sessions, scope_type='school_architecture_validation',
                    scope_definition={'session_ids': sorted(row.pk for row in sessions)}, force=True)
                coverage_attempts = list(run.attempt_results.all())
                coverage_sessions = list(run.session_results.all())
                result['coverage_attempt_count'] = len(coverage_attempts)
                result['coverage_outcome_usable_count'] = sum(row.analysis_eligibility['outcome']['usable'] for row in coverage_attempts)
                result['coverage_session_status_counts'] = dict(Counter(row.overall_status for row in coverage_sessions))
                result['coverage_attempt_status_counts'] = dict(Counter(row.overall_status for row in coverage_attempts))
                checks['coverage_exact_fixture_scope'] = (
                    {row.session_id for row in coverage_sessions} == {row.pk for row in sessions}
                    and {row.attempt_id for row in coverage_attempts} == {row.pk for row in attempts})
                checks['coverage_has_no_authoritative_contradictions'] = all(
                    row.overall_status != 'invalid' for row in [*coverage_sessions, *coverage_attempts])
                checks['coverage_outcome_matches_grade_availability'] = all(
                    row.analysis_eligibility['outcome']['usable'] == grade_ready[row.attempt_id]
                    and row.analysis_eligibility['outcome']['status'] != 'invalid' for row in coverage_attempts)
                result['raw_fact_integrity_passed'] = checks['coverage_has_no_authoritative_contradictions']
                result['raw_fact_integrity_failed'] = not result['raw_fact_integrity_passed']
                result['passed'] = all(checks.values())
                result['status'] = ('raw_fact_mismatch' if result['raw_fact_integrity_failed'] else
                                    ('passed' if result['passed'] else 'mismatch'))
                transaction.set_rollback(True)
        except CommandError:
            raise
        except Exception:
            # Error text can contain student identifiers, SQL or private answers.
            result['status'] = 'read_failed'
            result['passed'] = False
            result['checks']['services_read_successfully'] = False
        return result
