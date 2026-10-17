"""Reconcile frozen capacity inputs inside one read-only PostgreSQL snapshot."""
import hashlib
import json
import os
from contextlib import nullcontext
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.assessment.capacity_reconciliation import reconcile_capacity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


class Command(BaseCommand):
    help = 'Verify isolated protocol ownership, preserved material, final submission and current statistics without writes.'

    def add_arguments(self, parser):
        for name in ('fixture', 'plan', 'records', 'output'):
            parser.add_argument('--' + name, required=True)
        parser.add_argument('--skip-statistics', action='store_true')
        parser.add_argument('--browser-records', help='Separate normalized browser JSONL witnesses; retained in the same full denominator.')
        parser.add_argument('--evidence-output', help='New private JSONL artifact of selected-cohort DB facts before baseline restore.')

    def handle(self, *args, **options):
        paths = {key: Path(options[key]) for key in ('fixture', 'plan', 'records', 'output')}
        if paths['output'].exists():
            raise CommandError('Verification output already exists; use a new observation output.')
        try:
            fixture, plan = [json.loads(paths[key].read_text(encoding='utf-8-sig')) for key in ('fixture', 'plan')]
            if (digest({key: value for key, value in plan.items() if key != 'plan_id'}) != plan['plan_id']
                    or hashlib.sha256(paths['fixture'].read_bytes()).hexdigest() != plan['fixture_sha256']
                    or digest(fixture['fixture_spec']) != fixture['plan_sha']
                    or fixture['plan_sha'] != plan['fixture_spec_sha256']):
                raise ValueError('Frozen plan or fixture hash differs')
            records = [json.loads(line) for line in paths['records'].read_text(encoding='utf-8-sig').splitlines() if line.strip()]
            if options.get('browser_records'):
                paths['browser_records'] = Path(options['browser_records'])
                records.extend(json.loads(line) for line in paths['browser_records'].read_text(encoding='utf-8-sig').splitlines() if line.strip())
            evidence_path = Path(options['evidence_output']) if options.get('evidence_output') else None
            evidence_context = (os.fdopen(os.open(evidence_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600),
                                'w', encoding='utf-8') if evidence_path else nullcontext(None))
            with evidence_context as evidence:
                report = reconcile_capacity(plan, fixture, records, require_statistics=not options['skip_statistics'],
                    evidence_output=evidence)
            if evidence_path:
                report['independent_evidence'] = {'path': str(evidence_path),
                    'sha256': hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                    'scope': 'planned cohort only; same repeatable-read read-only transaction',
                    'format': 'jsonl', 'contains_private_synthetic_responses': True}
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise CommandError(str(error)) from error
        report['inputs_sha256'] = {key: hashlib.sha256(path.read_bytes()).hexdigest() for key, path in paths.items() if key != 'output'}
        report['source_sha256'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in (
            Path(__file__), Path(__file__).resolve().parents[2] / 'capacity_reconciliation.py')}
        with paths['output'].open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
        self.stdout.write(json.dumps({key: report[key] for key in (
            'planned', 'clean', 'with_differences', 'integrity_passed', 'workflow_complete', 'safety_stop', 'stats_ready', 'stats')}))
