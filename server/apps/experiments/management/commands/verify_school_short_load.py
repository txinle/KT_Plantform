import json
import hashlib
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import connection

from apps.accounts.models import User
from apps.assessment.models import ExperimentSession, Attempt, AssessmentEvent, SubmissionEvent
from apps.experiments.models import ClassExperimentTask


class Command(BaseCommand):
    help = 'Read-only aggregate persistence verification for isolated short-storm fixtures.'

    def add_arguments(self, parser):
        parser.add_argument('--manifest', required=True)
        parser.add_argument('--output', required=True)
        parser.add_argument('--cold-manifest', action='append', default=[])

    def handle(self, *args, **options):
        if connection.settings_dict['NAME'] != 'kt_e2e':
            raise CommandError('Only direct isolated database kt_e2e is permitted')
        fixture = json.loads(Path(options['manifest']).read_text())
        ledger = json.loads(Path(options['manifest']).with_suffix('.ledger.json').read_text())
        n = fixture['student_count']
        if n not in (3, 50, 150, 300) or fixture.get('database') != 'kt_e2e' or ledger['task_id'] != fixture['task_id'] or ledger['source_config'] != fixture['source_config']:
            raise CommandError('Dedicated short-load ledger binding required')
        task = ClassExperimentTask.objects.get(pk=fixture['task_id'])
        users = set(User.objects.filter(username__in=fixture['usernames']).values_list('pk', flat=True))
        sessions = list(ExperimentSession.objects.filter(class_task=task).values('pk', 'student_id', 'status', 'terminal_at'))
        ids = {row['pk'] for row in sessions}
        submitted = list(Attempt.objects.filter(session_id__in=ids).values('pk', 'session_id', 'status', 'final_answer', 'submission_token', 'submitted_at'))
        expected_events = [(int(session), token) for session, row in ledger['sessions'].items() for token in row['event_ids']]
        expected_tokens = [token for row in ledger['sessions'].values() for token in row['submission_tokens']]
        event_rows = set((session, str(token)) for session, token in AssessmentEvent.objects.filter(event_id__in=[token for _, token in expected_events]).values_list('session_id', 'event_id'))
        persisted_events = {str(row.event_id): row for row in AssessmentEvent.objects.filter(event_id__in=[token for _, token in expected_events])}
        envelope_matches = all(str(event.client_instance_id) == expected['client_instance_id']
            and event.client_sequence_no == expected['client_sequence_no'] and expected['acknowledged'] is True
            and hashlib.sha256(json.dumps(event.payload, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest() == expected['payload_sha256']
            for row in ledger['sessions'].values() for token, expected in row['events'].items()
            if (event := persisted_events.get(token)) is not None)
        stored_tokens = [str(row['submission_token']) for row in submitted if row['submission_token']]
        submission_rows = SubmissionEvent.objects.filter(attempt__session_id__in=ids).count()
        checks = {'all_sessions': len(sessions) == n and len(ids) == n and {row['student_id'] for row in sessions} == users and len(users) == n,
            'all_terminal': all(row['status'] == ExperimentSession.Status.COMPLETED and row['terminal_at'] is not None for row in sessions),
            'all_answers_persisted': len(submitted) == n * 20 and all(row['status'] == Attempt.Status.SUBMITTED and row['final_answer'] == 'A' and row['submitted_at'] for row in submitted),
            'zero_missing_events': len(expected_events) == n * 20 and set(expected_events) == event_rows,
            'zero_duplicate_events': len(set(expected_events)) == n * 20 and len(event_rows) == n * 20,
            'event_payload_envelope_and_ack_match': envelope_matches and len(persisted_events) == n*20,
            'zero_missing_submissions': len(expected_tokens) == n * 20 and set(expected_tokens) == set(stored_tokens),
            'zero_duplicate_submissions': len(set(expected_tokens)) == n * 20 and len(stored_tokens) == n * 20 and submission_rows == n * 20,
            'ledger_sessions_match': set(map(int, ledger['sessions'])) == ids,
            'formal_tasks_remain_cold': all(not ExperimentSession.objects.filter(class_task_id=json.loads(Path(path).read_text())['task_id']).exists() for path in options['cold_manifest'])}
        result = {'student_count': n, 'source_config': fixture['source_config'], 'database': 'kt_e2e', 'read_only': True,
            'session_count': len(sessions), 'answer_count': len(submitted), 'event_count': len(event_rows),
            'submission_count': submission_rows, 'checks': checks, 'passed': all(checks.values())}
        with Path(options['output']).open('x', encoding='utf-8') as output:
            json.dump(result, output, indent=2)
        self.stdout.write(json.dumps(result))
        if not result['passed']:
            raise CommandError('Short-load persistence verification failed')
