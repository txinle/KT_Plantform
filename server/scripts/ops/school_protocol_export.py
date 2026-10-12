#!/usr/bin/env python3
"""Bounded real export/download reconciliation for a private kt_e2e school fixture."""
import argparse
import csv
import hashlib
import http.cookiejar
import io
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
import zipfile


class ExportVerificationError(RuntimeError):
    """Only stable non-sensitive failure codes may enter the result report."""


class Budget:
    def __init__(self, seconds):
        if not 0 < seconds <= 300:
            raise ExportVerificationError('invalid_budget')
        self.deadline = time.monotonic() + seconds

    def remaining(self):
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise ExportVerificationError('deadline_exceeded')
        return remaining


def require_origin(origin):
    if origin != 'http://127.0.0.1:18080':
        raise ExportVerificationError('isolated_origin_required')
    return origin


def require_manifest_binding(fixture, claim, source, manifest_sha256):
    from scripts.load.capture_protocol import CONTRACT
    if (fixture.get('database') != 'kt_e2e' or fixture.get('capture_protocol') != CONTRACT
            or fixture.get('student_count') not in (3, 10, 180) or fixture.get('item_count') != 20
            or not isinstance(fixture.get('run_id'), str) or not fixture['run_id'].startswith('arch-')
            or fixture.get('source_config') != source or not isinstance(source, dict)
            or any(not re.fullmatch('[a-f0-9]{64}', source.get(key, ''))
                   for key in ('source_sha256', 'frontend_sha256', 'config_sha256'))
            or not re.fullmatch(r'/static/spa-assets/capture-shell-[a-f0-9]{20}\.html', source.get('shell_url', ''))
            or claim.get('task_id') != fixture.get('task_id') or claim.get('run_id') != fixture['run_id']
            or claim.get('manifest_sha256') != manifest_sha256
            or len(set(fixture.get('usernames', []))) != fixture['student_count']):
        raise ExportVerificationError('fixture_source_claim_mismatch')


def require_database():
    from django.db import connection
    if (os.environ.get('POSTGRES_DB') != 'kt_e2e' or connection.vendor != 'postgresql'
            or connection.settings_dict['NAME'] != 'kt_e2e'):
        raise ExportVerificationError('isolated_database_required')


def load_binding(args):
    from apps.experiments.management.commands.verify_school_architecture_results import require_binding
    from apps.accounts.models import User
    from apps.experiments.models import ClassExperimentTask
    require_database()
    raw = args.manifest.read_bytes()
    fixture = json.loads(raw)
    source = json.loads(args.source_config.read_bytes())
    claim = json.loads((args.manifest.parent / f'.school-task-{fixture["task_id"]}.claim.json').read_bytes())
    require_manifest_binding(fixture, claim, source, hashlib.sha256(raw).hexdigest())
    ledger = json.loads(args.manifest.with_suffix('.ledger.json').read_bytes())
    sessions = require_binding(fixture, ledger)
    if (ledger.get('incomplete') is not False or len(sessions) != fixture['student_count']
            or set(ledger['sessions']) != {str(row.pk) for row in sessions}
            or any(row.status != 'completed' or row.terminal_at is None for row in sessions)
            or any(row.get('complete') is not True for row in ledger['sessions'].values())):
        raise ExportVerificationError('terminal_fixture_required')
    task = ClassExperimentTask.objects.select_related('experiment__course__owner').get(pk=fixture['task_id'])
    teacher = task.experiment.course.owner
    if (teacher.username != 'load-teacher-' + fixture['run_id'] or teacher.role != User.Role.TEACHER
            or not teacher.is_active or not teacher.check_password(fixture.get('password', ''))
            or task.experiment.owner_id != teacher.pk
            or task.experiment.course.code != 'LOAD-' + fixture['run_id']
            or ClassExperimentTask.objects.filter(experiment_id=task.experiment_id).count() != 1):
        raise ExportVerificationError('dedicated_fixture_owner_required')
    return fixture, teacher, sessions


def require_job(job, fixture, teacher):
    if (job.course_id != fixture['course_id'] or job.experiment_id != fixture['experiment_id']
            or job.created_by_id != teacher.pk or job.package != 'experiment'
            or job.snapshot.filter_definition != {'scope_version': 2, 'experiment_id': fixture['experiment_id'],
                'class_mode': 'single', 'class_id': fixture['class_id']}
            or job.snapshot.source_bundle.get('version') != 2):
        raise ExportVerificationError('export_job_scope_mismatch')


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, newurl):
        return None


class LocalHTTP:
    def __init__(self, origin, budget):
        self.origin, self.budget = require_origin(origin), budget
        self.cookies = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.cookies), NoRedirect())

    def request(self, path, method='GET', body=None, download=None):
        if not path.startswith('/') or path.startswith('//'):
            raise ExportVerificationError('local_path_required')
        headers = {'Accept': 'application/json', 'Referer': self.origin + '/'}
        data = None
        if body is not None:
            headers.update({'Content-Type': 'application/json', 'X-CSRFToken': next(
                (cookie.value for cookie in self.cookies if cookie.name == 'csrftoken'), '')})
            data = json.dumps(body).encode()
        request = urllib.request.Request(self.origin + path, headers=headers, method=method, data=data)
        try:
            response = self.opener.open(request, timeout=min(35, self.budget.remaining()))
        except urllib.error.HTTPError as error:
            response = error
        if response.code not in (200, 201):
            response.close()
            raise ExportVerificationError('http_request_rejected')
        with response:
            if download is not None:
                size = 0
                with private_open(download, binary=True) as target:
                    while chunk := response.read(1024 * 1024):
                        self.budget.remaining()
                        size += len(chunk)
                        if size > 512 * 1024 * 1024:
                            raise ExportVerificationError('download_size_exceeded')
                        target.write(chunk)
                if size == 0 or 'zip' not in response.headers.get('Content-Type', ''):
                    raise ExportVerificationError('download_zip_required')
                return {'download_bytes': size, 'http_status': response.code}
            raw = response.read(4 * 1024 * 1024 + 1)
            if len(raw) > 4 * 1024 * 1024:
                raise ExportVerificationError('http_json_size_exceeded')
            envelope = json.loads(raw)
            return envelope.get('data', envelope)

    def login(self, teacher, password):
        self.request('/api/v1/auth/csrf/')
        context = self.request('/api/v1/auth/login/', 'POST', {'username': teacher.username, 'password': password})
        if (context.get('user', {}).get('role') != 'teacher'
                or not any(cookie.name == 'sessionid' and cookie.value for cookie in self.cookies)):
            raise ExportVerificationError('teacher_session_required')


def private_open(path, *, binary=False):
    handle = path.open('xb' if binary else 'x', **({} if binary else {'encoding': 'utf-8'}))
    os.chmod(path, 0o600)
    return handle


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


class CSVHash:
    def __init__(self, headers):
        self.digest = hashlib.sha256()
        self.digest.update(b'\xef\xbb\xbf')
        self.writer = csv.writer(self)
        self.writer.writerow(headers)
        self.count = 0

    def write(self, text):
        self.digest.update(text.encode('utf-8'))

    def row(self, values):
        self.writer.writerow(values)
        self.count += 1


def verify_archive(job, archive_path, sessions, budget):
    from apps.analytics.models import ResearchSessionBehavior, ResearchSessionMaterialization, ResearchSessionResponse
    from apps.analytics.research_contract import FILES
    from apps.analytics.research_dataset import read_research_bundle
    from apps.analytics.research_materialization import PROJECTION_VERSION, _scope_for_session
    from apps.analytics.research_export_v2 import _content_hash, _session_reference
    from apps.analytics.services import _source_bundle_hash
    frozen = job.snapshot.source_bundle['research_export_v2']
    artifact_hash = file_hash(archive_path)
    if (job.status != 'succeeded' or job.finished_at is None
            or not job.artifact_sha256 or artifact_hash != job.artifact_sha256
            or _source_bundle_hash(job.snapshot.source_bundle) != job.snapshot.source_bundle_hash):
        raise ExportVerificationError('artifact_or_frozen_source_hash_mismatch')
    session_ids = {row.pk for row in sessions}
    materializations = list(ResearchSessionMaterialization.objects.filter(pk__in=frozen['materialization_ids'])
                            .select_related('session').order_by('session_id'))
    if ({row.session_id for row in materializations} != session_ids or len(materializations) != len(sessions)
            or any(row.status != 'succeeded' or not row.is_current or row.projection_version != PROJECTION_VERSION
                   or row.source_revision != row.session.research_source_revision for row in materializations)
            or sorted(frozen['materializations'], key=lambda row: row['id']) != sorted([
                {'id': str(row.pk), 'source_revision': row.source_revision} for row in materializations], key=lambda row: row['id'])):
        raise ExportVerificationError('materialization_revision_mismatch')
    contracts = {item['key']: item for item in FILES}
    with zipfile.ZipFile(archive_path) as archive:
        if len(archive.namelist()) != len(FILES) or set(archive.namelist()) != {item['path'] for item in FILES}:
            raise ExportVerificationError('package_entries_mismatch')
        if archive.testzip() is not None:
            raise ExportVerificationError('archive_crc_mismatch')
        course_files = {item['path']: archive.read(item['path']) for item in FILES
                        if item['key'] not in {'responses', 'behavior_events', 'session_bindings'}}
        session_file = {contracts['session_bindings']['path']: archive.read(contracts['session_bindings']['path'])}
        if (_content_hash(course_files) != frozen['course_asset_hash']
                or _content_hash(session_file) != frozen['experiment_session_asset_hash']):
            raise ExportVerificationError('frozen_asset_hash_mismatch')
        bindings = list(csv.reader(io.StringIO(next(iter(session_file.values())).decode('utf-8-sig'))))
        identities = {row[-1]: (row[1], row[2]) for row in bindings[1:]}
        expected_refs = {row.session_summary.get('session_ref') or _session_reference(row.session)
                         for row in materializations}
        if set(identities) != expected_refs or len(bindings) - 1 != len(sessions):
            raise ExportVerificationError('session_binding_rows_mismatch')
        expected = {key: CSVHash(contracts[key]['headers']) for key in ('responses', 'behavior_events')}
        for session in sorted(sessions, key=lambda item: item.pk):
            budget.remaining()
            raw, _ = read_research_bundle(_scope_for_session(session), include_course_catalog=False)
            for key, digest in expected.items():
                for original in raw['files'][key]:
                    values = list(original)
                    values[1:3] = identities[str(values[0])]
                    digest.row(values)
        fact_counts, fact_hashes = {}, {}
        for key, model in (('responses', ResearchSessionResponse), ('behavior_events', ResearchSessionBehavior)):
            budget.remaining()
            material_hash = CSVHash(contracts[key]['headers'])
            for item in model.objects.filter(materialization__in=materializations).order_by('materialization__session_id', 'ordinal', 'pk').iterator(chunk_size=1000):
                values = list(item.row)
                values[1:3] = identities[str(values[0])]
                material_hash.row(values)
            digest = hashlib.sha256()
            with archive.open(contracts[key]['path']) as body:
                for chunk in iter(lambda: body.read(1024 * 1024), b''):
                    budget.remaining()
                    digest.update(chunk)
            downloaded_hash = digest.hexdigest()
            if (downloaded_hash != material_hash.digest.hexdigest()
                    or downloaded_hash != expected[key].digest.hexdigest()
                    or material_hash.count != expected[key].count):
                raise ExportVerificationError('authoritative_fact_hash_mismatch')
            fact_counts[key], fact_hashes[key] = material_hash.count, downloaded_hash
    return {'passed': True, 'artifact_sha256': artifact_hash, 'artifact_bytes': archive_path.stat().st_size,
        'source_bundle_sha256': job.snapshot.source_bundle_hash, 'response_count': fact_counts['responses'],
        'behavior_count': fact_counts['behavior_events'], 'materialization_count': len(materializations),
        'fact_sha256': fact_hashes, 'checks': {'archive_crc_valid': True, 'exact_package_entries': True,
            'frozen_assets_match': True, 'materialization_revisions_match': True, 'authoritative_fact_hashes_match': True}}


def run_owned_worker(args):
    from apps.analytics.models import ExportJob
    from apps.analytics.services import run_export_job
    fixture, teacher, _ = load_binding(args)
    job = ExportJob.objects.select_related('snapshot').get(pk=uuid.UUID(args.worker_job_id))
    require_job(job, fixture, teacher)
    print(json.dumps({'job_id': str(job.pk), 'initial_status': job.status,
                      'engine_invoked': job.status == 'queued'}), flush=True)
    if job.status == 'queued':
        # This is the production worker engine's targeted claim/lease/materialize/finalize path.
        run_export_job(job)
    elif job.status not in ('running', 'succeeded'):
        raise ExportVerificationError('worker_job_not_runnable')


def execute(args):
    from apps.analytics.models import ExportJob
    fixture, teacher, sessions = load_binding(args)
    budget = Budget(args.timeout)
    http = LocalHTTP(args.base_url, budget)
    http.login(teacher, fixture['password'])
    prefix = f'/api/v1/teacher/courses/{fixture["course_id"]}/data/export-jobs/'
    created = http.request(prefix, 'POST', {'package': 'experiment', 'confirm_identified': True,
        'scope': {'scope_version': 2, 'experiment_id': fixture['experiment_id'],
                  'class_mode': 'single', 'class_id': fixture['class_id']}})
    job_id = str(uuid.UUID(created['job_id']))
    job = ExportJob.objects.select_related('snapshot').get(pk=job_id)
    require_job(job, fixture, teacher)
    worker_log = args.output.with_suffix('.worker.log')
    with private_open(worker_log) as log:
        worker = subprocess.Popen([sys.executable, str(Path(__file__).resolve()),
            '--manifest', str(args.manifest), '--source-config', str(args.source_config),
            '--output', str(args.output), '--worker-job-id', job_id], stdout=log, stderr=log)
        try:
            while True:
                budget.remaining()
                status = http.request(prefix + job_id + '/')
                if status.get('status') == 'succeeded':
                    break
                if status.get('status') not in ('queued', 'running'):
                    raise ExportVerificationError('export_job_failed')
                time.sleep(min(2, budget.remaining()))
            worker_code = worker.wait(timeout=min(35, budget.remaining()))
            download_path = status.get('download_url')
            if download_path != prefix + job_id + '/download/':
                raise ExportVerificationError('download_scope_mismatch')
            archive_path = args.output.with_suffix('.zip')
            transfer = http.request(download_path, download=archive_path)
            job.refresh_from_db()
            result = verify_archive(job, archive_path, sessions, budget)
            if result['response_count'] != fixture['student_count'] * fixture['item_count']:
                raise ExportVerificationError('fixture_response_count_mismatch')
            log.flush()
            try:
                worker_evidence = json.loads(worker_log.read_text(encoding='utf-8').splitlines()[0])
            except (OSError, ValueError, IndexError):
                worker_evidence = {}
            invoked = worker_evidence.get('job_id') == job_id and worker_evidence.get('engine_invoked') is True
            result.update(database='kt_e2e', evidence_kind='production_export_job_authenticated_download',
                run_id=fixture['run_id'], task_id=fixture['task_id'], source_config=fixture['source_config'],
                job_id=job_id, snapshot_id=str(job.snapshot_id), http_status=transfer['http_status'],
                download_bytes=transfer['download_bytes'], worker_exit_code=worker_code,
                worker_path='targeted_production_run_export_job' if invoked and worker_code == 0 else 'concurrent_production_worker',
                targeted_engine_invoked=invoked, retained_archive=archive_path.name, worker_pid=worker.pid)
            with private_open(args.output) as target:
                json.dump(result, target, indent=2)
        finally:
            if worker.poll() is None:
                worker.terminate()
                try:
                    worker.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    worker.kill()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--source-config', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--base-url', default='http://127.0.0.1:18080')
    parser.add_argument('--timeout', type=float, default=300)
    parser.add_argument('--child', action='store_true', help=argparse.SUPPRESS)
    parser.add_argument('--worker-job-id', help=argparse.SUPPRESS)
    args = parser.parse_args()
    code = 1
    try:
        require_origin(args.base_url)
        Budget(args.timeout)
        if args.child or args.worker_job_id:
            sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
            os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
            import django
            django.setup()
            run_owned_worker(args) if args.worker_job_id else execute(args)
            return 0
        if args.output.exists() or args.output.with_suffix('.zip').exists() or args.output.with_suffix('.worker.log').exists():
            raise ExportVerificationError('fresh_output_required')
        # The supervisor enforces the bound across Django setup, DB work, HTTP, download and hashing.
        child = subprocess.Popen([sys.executable, *sys.argv, '--child'], start_new_session=True)
        try:
            code = child.wait(timeout=max(0.1, args.timeout - 5))
        except subprocess.TimeoutExpired:
            if os.name == 'posix':
                os.killpg(child.pid, signal.SIGTERM)
            else:
                child.terminate()
            try:
                child.wait(timeout=2)
            except subprocess.TimeoutExpired:
                if os.name == 'posix':
                    os.killpg(child.pid, signal.SIGKILL)
                else:
                    child.kill()
            raise ExportVerificationError('deadline_exceeded')
        if code != 0:
            raise ExportVerificationError('export_verification_failed')
        result = json.loads(args.output.read_bytes())
        print(json.dumps({'passed': result.get('passed') is True, 'artifact_sha256': result.get('artifact_sha256')}))
        return 0
    except Exception as error:
        failure = str(error) if isinstance(error, ExportVerificationError) else 'export_verification_failed'
        if args.worker_job_id:
            print(json.dumps({'passed': False, 'failure_code': failure}))
        elif not args.output.exists():
            report = {'passed': False, 'failure_code': failure, 'database': 'kt_e2e'}
            retained = args.output.with_suffix('.zip')
            if retained.is_file():
                report.update(retained_archive=retained.name, artifact_bytes=retained.stat().st_size,
                              artifact_sha256=file_hash(retained))
            with private_open(args.output) as target:
                json.dump(report, target)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
