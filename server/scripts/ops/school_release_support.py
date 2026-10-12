#!/usr/bin/env python3
"""Private promotion preparation; never emit environment or database secrets."""
import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path, PurePosixPath


PRODUCTION = Path('/data/kt-platform/KT_Plantform')
SERVICES = ('web', 'student-read', 'teacher-read', 'scheduler', 'analytics-worker',
            'student-comparison-worker', 'publication-worker', 'export-worker', 'nginx')
EXCLUDED = {'.env', 'school-test.env', '.git', '.venv', '__pycache__', 'media', 'staticfiles'}
PRESSURE_ROLES = ('web', 'student-read', 'teacher-read', 'analytics-worker',
                  'student-comparison-worker', 'export-worker')


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open('rb') as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def extract_verified_source(archive_path, expected_sha256, candidate, release):
    archive_path, candidate, release = map(Path, (archive_path, candidate, release))
    digest = file_sha256(archive_path)
    if digest != expected_sha256:
        raise ValueError('Candidate archive SHA256 mismatch')
    entries = []
    with tarfile.open(archive_path) as archive:
        for entry in archive:
            path = PurePosixPath(entry.name)
            if path.is_absolute() or '..' in path.parts or entry.issym() or entry.islnk():
                raise ValueError('Unsafe archive entry')
            if entry.isfile():
                entries.append((entry, path))
        roots = [path.parent for _, path in entries if path.name == 'Dockerfile.school']
        if len(roots) != 1:
            raise ValueError('Archive must contain exactly one school source root')
        source_root = roots[0]
        contents = []
        for entry, path in entries:
            try:
                relative = path.relative_to(source_root)
            except ValueError:
                raise ValueError('Archive contains files outside source root') from None
            if any(part in EXCLUDED for part in relative.parts) or relative.suffix == '.pyc':
                continue
            if relative.parts[:2] in (('frontend', 'node_modules'), ('frontend', 'dist')):
                continue
            content = archive.extractfile(entry).read()
            tested_path = candidate.joinpath(*relative.parts)
            if tested_path.is_symlink() or not tested_path.is_file():
                raise ValueError('Tested candidate differs from accepted archive')
            tested_content = tested_path.read_bytes()
            # Linux shell entrypoints may be explicitly CRLF-normalized after
            # Windows archive extraction; only line endings are canonicalized.
            if relative.suffix == '.sh':
                content = content.replace(b'\r\n', b'\n')
                tested_content = tested_content.replace(b'\r\n', b'\n')
            if tested_content != content:
                raise ValueError('Tested candidate differs from accepted archive: ' + str(relative))
            contents.append((relative, content, entry.mode))
    release.mkdir(parents=True, exist_ok=False)
    for relative, content, mode in contents:
        target = release.joinpath(*relative.parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(mode & 0o777)
    return digest


def require_acceptance(recorded, evaluated, status):
    if not recorded.get('passed') or not evaluated.get('passed') or status.get('state') != 'passed':
        raise ValueError('Formal acceptance has not passed')
    if recorded != evaluated:
        raise ValueError('Formal acceptance differs from independently evaluated evidence')


def accepted_postgres_resources(profile, source_sha256, run_id):
    kernel = profile.get('observed_kernel_limits', {})
    required = {'host_headroom', 'candidate_margin', 'combined_working_set_plus_margin'}
    if (profile.get('run_id') != run_id or profile.get('state') != 'passed-observed-fixed'
            or profile.get('source_sha256') != source_sha256
            or profile.get('formal_acceptance_passed') is not True
            or profile.get('pool_aliases_verified') is not True
            or profile.get('shared_postgres_read_only') is not True
            or profile.get('dynamic_cpu_control') is not False
            or set(kernel) != {'memory.max', 'memory.swap.max', 'cpu.max', 'cpu.weight'}
            or not re.fullmatch(r'max [1-9][0-9]*', kernel.get('cpu.max', ''))
            or any(not re.fullmatch(r'max|[0-9]+', kernel.get(name, '')) for name in ('memory.max','memory.swap.max'))
            or not re.fullmatch(r'[1-9][0-9]*', kernel.get('cpu.weight', ''))
            or not profile.get('samples')
            or any(row.get('limits_match') is not True
                   or not required <= row.get('memory_budget_checks', {}).keys()
                   or any(value is not True for value in row['memory_budget_checks'].values())
                   for row in profile['samples'])):
        raise ValueError('PostgreSQL accepted resource profile mismatch')
    return {'read_only': True, 'kernel_limits': kernel}


def require_promotion_evidence(report, source_sha256, run_id):
    report = Path(report)
    def read(name):
        return json.loads((report / name).read_text())

    status, provenance = read('status.json'), read('cached-build-provenance.json')
    frontend = provenance.get('frontend_archive_sha256', '')
    if (status.get('state') != 'passed' or status.get('run_id') != run_id
            or provenance.get('run_id') != run_id
            or provenance.get('candidate_archive_sha256') != source_sha256
            or not re.fullmatch('[a-f0-9]{64}', frontend)):
        raise ValueError('Accepted source/frontend evidence mismatch')
    profile = read('postgres-resource-profile.json')
    resources = accepted_postgres_resources(profile, source_sha256, run_id)
    config = read('source-config.json')
    def validate_config(value, formal=False):
        canonical = {key: item for key, item in value.items() if key != 'config_sha256'}
        digest = hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        roles = value.get('roles', {})
        if (value.get('source_sha256') != source_sha256 or value.get('frontend_sha256') != frontend
                or value.get('run_id') != run_id or value.get('config_sha256') != digest
                or value.get('dynamic_cpu_control') is not False or value.get('shared_postgres_read_only') is not True
                or not value.get('shared_postgres_cgroup', {}).get('cpu_max', '').startswith('max ')
                or not value.get('actual_postgres_settings')
                or any(roles.get(role, {}).get('workers') != workers for role, workers in {'web':12,'student-read':2,'teacher-read':1}.items())
                or any(roles.get(role, {}).get('actual_running') is not True
                       or not re.fullmatch('sha256:[a-f0-9]{64}', roles[role].get('image_id', ''))
                       or not isinstance(roles[role].get('memory_bytes'), int) or roles[role]['memory_bytes'] <= 0
                       for role in ('web','student-read','teacher-read'))
                or roles['web'].get('cpus') != 0 or not roles['web'].get('cgroup', {}).get('cpu_max','').startswith('max ')
                or formal and (value.get('scope') != 'formal_school_acceptance'
                    or roles.get('analytics-worker', {}).get('actual_running') is not True
                    or roles['analytics-worker'].get('replicas') != 1)):
            raise ValueError('Fixed source resource configuration mismatch')
    validate_config(config, formal=True)
    phases = {name: read(name) for name in ('baseline.json', 'mixed.json')}
    for phase in phases.values():
        warmup = phase.get('warmup_started_at')
        if (not isinstance(warmup, (int, float))
                or not profile['applied_at'] <= warmup <= phase['started_at'] < phase['ended_at'] <= profile['ended_at']
                or phase['ended_at'] - phase['started_at'] < 1800 or phase.get('student_count') != 300):
            raise ValueError('PostgreSQL accepted profile does not cover formal authentication and workload')
    for name, phase in phases.items():
        observed = read('baseline-observation.json' if name == 'baseline.json' else 'observation.json')
        backlog = observed.get('backlog_samples', [])
        checks = observed.get('peak_memory_budget', {}).get('checks', {})
        swap_start, swap_end = observed.get('swap_start_bytes'), observed.get('swap_end_bytes')
        if (observed.get('source') not in ('docker_events', 'docker_inspect')
                or not observed.get('started_at', float('inf')) <= phase['warmup_started_at']
                or not observed.get('ended_at', 0) >= phase['ended_at']
                or observed.get('oom_kills') != 0 or not observed.get('memory_cpu_samples')
                or not isinstance(swap_start, (int, float)) or not isinstance(swap_end, (int, float))
                or swap_end > swap_start or not backlog or not backlog[-1].get('counts')
                or any(value != 0 for value in backlog[-1]['counts'].values())
                or not {'host_headroom','candidate_margin','combined_working_set_plus_margin'} <= checks.keys()
                or any(value is not True for value in checks.values())):
            raise ValueError('Formal runtime observation does not cover authentication and workload safely')
    short_config = None
    for count in (3, 50, 150, 300):
        for suffix in ('', '-persistence'):
            evidence = read(f'short-{count}{suffix}.json')
            config = evidence.get('source_config', {})
            validate_config(config)
            if (evidence.get('passed') is not True or evidence.get('student_count') != count
                    or not evidence.get('checks')
                    or any(value is not True for value in evidence['checks'].values())
                    or config.get('source_sha256') != source_sha256 or config.get('frontend_sha256') != frontend
                    or (short_config is not None and config != short_config)):
                raise ValueError('Independent short HTTP/persistence acceptance mismatch')
            short_config = config
    for kind in ('sql','auth','visual'):
        evidence = read(kind + '-verification.json')
        if (evidence.get('run_id') != run_id or evidence.get('source_sha256') != source_sha256
                or evidence.get('frontend_sha256') != frontend or not evidence.get('checks')
                or evidence.get('environment') != 'school_isolated_acceptance'
                or any(value is not True for value in evidence['checks'].values())
                or not evidence.get('measurements')):
            raise ValueError('SQL/authenticated/visual verification evidence missing or mismatched')
        rows = evidence['measurements']
        if kind == 'sql' and (any(row.get('status') != 200 or not row.get('path')
                or not isinstance(row.get('elapsed_seconds'), (int,float)) or row['elapsed_seconds'] < 0
                or not isinstance(row.get('query_count'), int) or row['query_count'] < 0
                or not isinstance(row.get('response_bytes'), int) or row['response_bytes'] <= 0 for row in rows)
                or not any(row.get('plan_summary', {}).get('node_types') for row in rows)):
            raise ValueError('Actual SQL timing and plan measurements missing')
        if kind == 'auth' and (evidence.get('auth_method') != 'session_cookie'
                or not {'student','teacher'} <= {row.get('role') for row in rows if row.get('status') == 200}
                or any(not row.get('path') or row.get('status') not in (200,201,204,403) for row in rows)):
            raise ValueError('School authenticated session measurements missing')
        if kind == 'visual':
            expected = {(role, viewport, zoom) for role in ('student','teacher')
                        for viewport, zoom in (((1440,900),1),((390,844),1),((1440,900),2))}
            actual = {(row.get('role'), tuple(row.get('viewport',[])), row.get('zoom')) for row in rows
                      if re.fullmatch('[a-f0-9]{64}', row.get('screenshot_sha256',''))}
            if not expected <= actual:
                raise ValueError('School viewport and zoom screenshot evidence missing')
    return {'frontend_sha256': frontend, 'accepted_postgres_resources': resources,
            'accepted_postgres_kernel_limits': profile['observed_kernel_limits'],
            'formal_source_config': read('source-config.json'), 'short_source_config': short_config}


def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()


def inspect_project(project):
    ids = docker('ps', '-aq', '--filter', 'label=com.docker.compose.project=' + project).split()
    if not ids:
        raise ValueError('Required Compose project has no containers')
    return json.loads(docker('inspect', *ids))


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2))
    Path(path).chmod(0o600)


def pressure_service_unit(release, private, public):
    release, private, public = map(Path, (release, private, public))
    run = private.name
    if (not re.fullmatch(r'[0-9]{8}T[0-9]{6}Z-[0-9]+', run)
            or release != Path('/data/kt-platform/school-releases') / run
            or private != Path('/home/ktdata/school-promotion-private') / run
            or public != Path('/home/ktdata/school-promotion-reports') / run):
        raise ValueError('Pressure service requires one fixed promotion binding')
    return ('[Unit]\nDescription=KT school capture priority controller\nAfter=docker.service\nRequires=docker.service\n'
            '[Service]\nType=simple\nUser=root\nWorkingDirectory=/data/kt-platform/KT_Plantform\n'
            f'ExecStart=/usr/bin/python3 {release}/scripts/ops/school_capture_priority.py '
            f'--project kt_plantform --production-binding {private}/pressure-binding.json '
            f'--output {public}/pressure.json\nRestart=on-failure\nRestartSec=5\n'
            'KillSignal=SIGTERM\nTimeoutStopSec=45\nUMask=0077\n'
            '[Install]\nWantedBy=multi-user.target\n')


def verify_pressure_service(path):
    path = Path(path)
    if path != Path('/etc/systemd/system/kt-school-pressure.service'):
        raise ValueError('Unknown pressure service path')
    if path.is_symlink() or path.stat().st_uid != 0 or path.stat().st_mode & 0o022:
        raise ValueError('Unknown pressure service ownership')
    text = path.read_text()
    starts = [line.split('=', 1)[1] for line in text.splitlines() if line.startswith('ExecStart=')]
    if len(starts) != 1:
        raise ValueError('Unknown pressure service command')
    tokens = shlex.split(starts[0])
    if len(tokens) != 8 or tokens[0] != '/usr/bin/python3':
        raise ValueError('Unknown pressure service command')
    controller, private, public = Path(tokens[1]), Path(tokens[5]).parent, Path(tokens[7]).parent
    release = controller.parents[2]
    if text != pressure_service_unit(release, private, public):
        raise ValueError('Unknown pressure service definition')
    binding_path = private / 'pressure-binding.json'
    for owned in (controller, binding_path):
        forbidden_mode = 0o077 if owned == binding_path else 0o022
        if owned.is_symlink() or owned.stat().st_uid != 0 or owned.stat().st_mode & forbidden_mode:
            raise ValueError('Unknown pressure service binding ownership')
    binding = json.loads(binding_path.read_text())
    if (binding.get('formal_accepted') is not True
            or binding.get('controller_sha256') != file_sha256(controller)):
        raise ValueError('Unknown pressure service source binding')
    return public / 'pressure.json'


def prepare_pressure_service(private, release, public):
    raise ValueError('Dynamic pressure controller deployment is disabled by fixed runtime policy')


def prepare(args):
    private, candidate, release, report = map(Path, (args.private, args.candidate, args.release, args.report))
    spec = importlib.util.spec_from_file_location('school_acceptance', candidate / 'scripts/load/school_mixed_300.py')
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(candidate / 'scripts/load'))
    spec.loader.exec_module(module)
    evaluator_spec = importlib.util.spec_from_file_location('school_final_evaluator', candidate / 'scripts/load/evaluate_school_load.py')
    evaluator = importlib.util.module_from_spec(evaluator_spec)
    evaluator_spec.loader.exec_module(evaluator)
    values = [json.loads((report / name).read_text()) for name in ('baseline.json','mixed.json','observation.json')]
    evaluated = module.evaluate(*values)
    evaluated['checks'].update(evaluator.baseline_checks(values[0], json.loads((report/'baseline-observation.json').read_text())))
    evaluated['checks']['mixed_observation_covers_auth_and_workload'] = values[2].get('started_at',float('inf')) <= values[1].get('warmup_started_at',0)
    evaluated['passed'] = all(evaluated['checks'].values())
    evaluated['source_config'] = json.loads((report/'source-config.json').read_text())
    sys.path.pop(0)
    require_acceptance(json.loads((report / 'acceptance.json').read_text()), evaluated,
                       json.loads((report / 'status.json').read_text()))
    accepted_evidence = require_promotion_evidence(report, args.sha256, report.name)
    binding_path = Path('/home/ktdata/school-cache-private') / report.name / 'cached-source-binding.json'
    binding = json.loads(binding_path.read_text())
    if (binding.get('run_id') != report.name or binding.get('candidate_archive_sha256') != args.sha256
            or binding.get('frontend_archive_sha256') != accepted_evidence['frontend_sha256']):
        raise ValueError('Accepted private source/frontend binding mismatch')
    production = inspect_project('kt_plantform')
    tested = inspect_project('kt-school-e2e')
    dbs = [item for item in production if item['Name'] == '/kt_plantform-db-1' and item['State']['Running']]
    webs = [item for item in production if item['Config']['Labels'].get('com.docker.compose.service') == 'web' and item['State']['Running']]
    if len(dbs) != 1 or len(webs) != 1:
        raise ValueError('Expected single live production database and web')
    db, web = dbs[0], webs[0]
    profile = json.loads((report / 'postgres-resource-profile.json').read_text())
    if profile.get('container_id') != db['Id']:
        raise ValueError('Accepted PostgreSQL container identity mismatch')
    labels = web['Config']['Labels']
    if Path(labels['com.docker.compose.project.working_dir']).resolve() != PRODUCTION:
        raise ValueError('Production Compose working directory mismatch')
    files = labels['com.docker.compose.project.config_files'].split(',')
    if not files or any(not Path(file).is_file() for file in files):
        raise ValueError('Production Compose files are unavailable')
    networks = set(db['NetworkSettings']['Networks']) & set(web['NetworkSettings']['Networks'])
    networks = [name for name in networks if json.loads(docker('network', 'inspect', name))[0].get('Labels', {}).get('com.docker.compose.project') == 'kt_plantform']
    if len(networks) != 1:
        raise ValueError('Production application network is ambiguous')
    volumes = {}
    for key, destinations in {'postgres_data': {'/var/lib/postgresql/data'}, 'media_data': {'/app/media', '/media'},
                              'frontend_assets': {'/static/spa-assets/assets'}}.items():
        matches = {mount['Name'] for item in production for mount in item['Mounts']
                   if mount['Type'] == 'volume' and mount['Destination'] in destinations}
        if len(matches) != 1:
            raise ValueError('Production volume identity is ambiguous: ' + key)
        volumes[key] = matches.pop()
    old_images, old_counts = {}, {}
    for item in production:
        service = item['Config']['Labels'].get('com.docker.compose.service')
        if not service:
            continue
        if service in old_images and old_images[service] != item['Image']:
            raise ValueError('Multiple production image versions for one service')
        old_images[service] = item['Image']
        old_counts[service] = old_counts.get(service, 0) + int(item['State']['Running'])
    accepted_images = {}
    for service in SERVICES:
        matches = {item['Image'] for item in tested if item['State']['Running'] and item['Config']['Labels'].get('com.docker.compose.service') == service}
        if len(matches) != 1:
            raise ValueError('Accepted image unavailable: ' + service)
        accepted_images[service] = matches.pop()
    recorded_images_path = report / 'candidate-image-ids.json'
    recorded_images = json.loads(recorded_images_path.read_text())
    if any(recorded_images.get(service) != image for service, image in accepted_images.items()):
        raise ValueError('Accepted test image IDs differ from recorded build')
    tested_poolers = {item['Image'] for item in tested if item['State']['Running'] and item['Config']['Labels'].get('com.docker.compose.service') == 'pgbouncer'}
    if len(tested_poolers) != 1:
        raise ValueError('Accepted PgBouncer image unavailable')
    accepted_images['pgbouncer'] = next(iter(tested_poolers))
    if recorded_images.get('pgbouncer') != accepted_images['pgbouncer']:
        raise ValueError('Accepted PgBouncer image differs from recorded build')
    if any(accepted_images[role] != definition['image_id']
           for role, definition in accepted_evidence['formal_source_config']['roles'].items()
           if role in accepted_images and definition.get('actual_running') is True):
        raise ValueError('Formal acceptance images differ from live accepted images')
    digest = extract_verified_source(args.archive, args.sha256, candidate, release)
    shutil.copyfile(PRODUCTION / '.env', release / '.env')
    (release / '.env').chmod(0o600)
    old_command = ['docker', 'compose', '--project-directory', str(PRODUCTION), '-p', 'kt_plantform']
    for file in files:
        old_command.extend(('-f', file))
    old_config = json.loads(subprocess.check_output([*old_command, 'config', '--format', 'json'], text=True))
    for service, definition in old_config['services'].items():
        if service not in old_images:
            continue
        definition['image'] = old_images[service]
        definition.pop('build', None)
        definition['pull_policy'] = 'never'
        definition.setdefault('deploy', {})['replicas'] = old_counts[service]
    write_json(private / 'rollback-compose.json', old_config)
    web_environment = {'ADVANCED_DATA_MATERIALIZED_ENABLED': '1', 'ADVANCED_DATA_MATERIALIZED_COURSE_IDS': '2',
                       'STUDENT_SHARED_RESULTS_ENABLED': '1', 'STUDENT_SHARED_RESULTS_COURSE_IDS': '2'}
    definitions = {service: {'image': image, 'pull_policy': 'never'} for service, image in accepted_images.items()}
    for service in SERVICES:
        if service != 'nginx':
            definitions[service]['environment'] = web_environment.copy()
    for role, workers in {'web': 12, 'student-read': 2, 'teacher-read': 1}.items():
        definitions[role]['environment']['GUNICORN_WORKERS'] = str(workers)
        definitions[role]['deploy'] = {'replicas': 1}
    definitions['analytics-worker']['deploy'] = {'replicas': 1}
    definitions['migrate'] = {'image': accepted_images['web'], 'pull_policy': 'never'}
    definitions['pgbouncer'] = {'image': tested_poolers.pop(), 'pull_policy': 'never'}
    definitions['pgbouncer']['volumes'] = [
        str(release / 'docker/pgbouncer-school.ini.template') + ':/school/pgbouncer.ini.template:ro',
        str(release / 'docker/pgbouncer-entrypoint.sh') + ':/school/pgbouncer-entrypoint.sh:ro']
    definitions['db'] = {'image': db['Image'], 'pull_policy': 'never'}
    write_json(release / 'docker-compose.production.json', {
        'services': definitions, 'volumes': {key: {'external': True, 'name': name} for key, name in volumes.items()},
        'networks': {'default': {'external': True, 'name': networks[0]}}})
    facts = {'archive_sha256': digest, 'production_network': networks[0], 'production_database_container': db['Id'],
             **accepted_evidence, 'auto_pressure_controller_deployed': False,
             'old_images': old_images, 'old_running_counts': old_counts, 'accepted_images': accepted_images,
             'volumes': volumes, 'old_db_memory': db['HostConfig']['Memory'], 'old_db_nano_cpus': db['HostConfig']['NanoCpus']}
    write_json(private / 'facts.json', facts)
    write_json(private / 'acceptance.json', evaluated)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('private', 'candidate', 'release', 'report', 'archive', 'sha256'):
        parser.add_argument('--' + name)
    parser.add_argument('--verify-pressure-unit')
    parser.add_argument('--prepare-pressure-service', action='store_true')
    try:
        args = parser.parse_args()
        if args.verify_pressure_unit:
            print(verify_pressure_service(args.verify_pressure_unit))
        elif args.prepare_pressure_service:
            prepare_pressure_service(args.private, args.release, args.report)
        else:
            if any(getattr(args, name) is None for name in ('private', 'candidate', 'release', 'report', 'archive', 'sha256')):
                parser.error('Promotion preparation requires all source and report arguments')
            prepare(args)
    except Exception as exc:
        # Docker errors and invalid dotenv/config can contain credentials.
        print('Promotion preparation failed: ' + type(exc).__name__)
        raise SystemExit(1)
