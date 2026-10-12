#!/usr/bin/env bash
# Authorized candidate acceptance only. No production cutover, source replacement,
# old materialization cleanup, database deletion, or production container recreation.
set -Eeuo pipefail
umask 077
for option in "$@"; do
    case "$option" in
        --reuse-fixtures) export SCHOOL_REUSE_FIXTURES=1 ;;
        --short-only) export SCHOOL_SHORT_ONLY=1 ;;
        *) exit 1 ;;
    esac
done
SHORT_ONLY=${SCHOOL_SHORT_ONLY:-1}
[[ "$SHORT_ONLY" == 1 ]] || { echo "Current approved entry is short-only." >&2; exit 1; }

CANDIDATE=/home/ktdata/school-cache-candidate
PROD_DB=kt_plantform-db-1
PROJECT=kt-school-e2e
RESUME_RUN_ID=${SCHOOL_RESUME_RUN_ID:-}
RUN_ID="${RESUME_RUN_ID:-$(date -u +%Y%m%dT%H%M%SZ)-$$}"
PRIVATE=/home/ktdata/school-cache-private/$RUN_ID
PUBLIC=/home/ktdata/school-cache-reports/$RUN_ID
STAGE=preflight
GUARD_PID=
OBSERVER_PID=
PRESSURE_PID=
PRESSURE_GUARD_PID=
MAIN_PID=$$

[[ $(id -u) == 0 ]] || { echo 'Run once through authenticated sudo.' >&2; exit 1; }
[[ $(readlink -f "$CANDIDATE") == "$CANDIDATE" ]] || { echo 'Unexpected candidate path.' >&2; exit 1; }
cd "$CANDIDATE"
[[ -f Dockerfile.school && -f docker-compose.school-test-external-db.yml ]] || exit 1
if [[ -z "$RESUME_RUN_ID" ]]; then
    [[ ! -e school-test.env && ! -L school-test.env ]] || { echo 'Existing test secret file: refusing replacement.' >&2; exit 1; }
else
    [[ ${SCHOOL_CACHED_BUILD:-0} == 1 && -f "$PRIVATE/cached-source-binding.json" ]] || { echo 'Resume must pass the owned cached-build wrapper.' >&2; exit 1; }
    [[ "$RESUME_RUN_ID" =~ ^[0-9]{8}T[0-9]{6}Z-[0-9]+$ && -d "$PUBLIC" && -L school-test.env ]] || exit 1
fi
id ktdata >/dev/null
command -v docker >/dev/null
command -v python3 >/dev/null
command -v setsid >/dev/null
[[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -ge 2097152 ]] || { echo 'Need at least 2GiB MemAvailable before starting.' >&2; exit 1; }
[[ -z $(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT") ]] || { echo 'Existing test project: refusing reuse.' >&2; exit 1; }
docker inspect "$PROD_DB" --format '{{.State.Running}}' | grep -qx true
PROD_WEB=$(docker ps -q --filter label=com.docker.compose.project=kt_plantform --filter label=com.docker.compose.service=web | head -n1)
[[ -n "$PROD_WEB" ]] || { echo 'Production web container not found.' >&2; exit 1; }
python3 - "$(docker compose version --short)" <<'PY'
import re, sys
parts = [int(part) for part in re.findall(r'\d+', sys.argv[1])[:3]]
assert tuple(parts) >= (2, 24, 4), 'Compose >=2.24.4 required'
PY
mkdir -p /home/ktdata/school-cache-private /home/ktdata/school-cache-reports
if [[ -z "$RESUME_RUN_ID" ]]; then mkdir "$PRIVATE" "$PUBLIC"; fi
chmod 755 /home/ktdata/school-cache-reports
chown ktdata:ktdata /home/ktdata/school-cache-reports
chmod 700 "$PRIVATE"
chmod 755 "$PUBLIC"
chown ktdata:ktdata "$PUBLIC"

status() {
    python3 - "$PUBLIC/status.json" "$1" "$STAGE" "$RUN_ID" "${2:-0}" <<'PY'
import datetime, json, os, sys
path, state, stage, run, code = sys.argv[1:]
body = {'state':state, 'stage':stage, 'run_id':run, 'exit_code':int(code),
        'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'production_cutover_performed':False}
with open(path+'.tmp','w') as out: json.dump(body,out,indent=2)
os.replace(path+'.tmp',path)
PY
    chmod 644 "$PUBLIC/status.json"
    chown ktdata:ktdata "$PUBLIC/status.json"
    chmod 644 "$PUBLIC"/*.json
    chown ktdata:ktdata "$PUBLIC"/*.json
    printf '%s %s\n' "$1" "$STAGE"
}

stop_test_only() {
    local ids
    ids=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT")
    if [[ -n "$ids" ]]; then docker stop $ids >"$PRIVATE/stop-test.log" 2>&1 || true; fi
}

finish() {
    local code=$?
    trap - EXIT TERM INT
    [[ -z "$GUARD_PID" ]] || kill "$GUARD_PID" 2>/dev/null || true
    [[ -z "$OBSERVER_PID" ]] || kill "$OBSERVER_PID" 2>/dev/null || true
    [[ -z "$PRESSURE_GUARD_PID" ]] || kill "$PRESSURE_GUARD_PID" 2>/dev/null || true
    if [[ -n "$PRESSURE_PID" ]]; then
        kill -TERM "$PRESSURE_PID" 2>/dev/null || true
        wait "$PRESSURE_PID" || code=1
        PRESSURE_PID=
    fi
    if [[ $code -ne 0 ]]; then
        stop_test_only
        python3 scripts/ops/school_failure_diagnostic.py --private "$PRIVATE" --public "$PUBLIC" --stage "$STAGE" --exit-code "$code" || true
        status failed "$code"
    fi
    exit "$code"
}
trap finish EXIT
trap 'exit 130' TERM INT

# Every command runs in its own process group so the memory guard can cancel a
# build/seed/load promptly without killing the caller's SSH or production jobs.
run_step() {
    STAGE=$1
    shift
    status running
    setsid "$@" >"$PRIVATE/$STAGE.log" 2>&1 &
    local child=$!
    printf '%s\n' "$child" >"$PRIVATE/active-process.pid"
    local code=0
    wait "$child" || code=$?
    rm -f "$PRIVATE/active-process.pid"
    return "$code"
}

memory_guard() {
    while kill -0 "$MAIN_PID" 2>/dev/null; do
        local available child
        available=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
        if [[ -z "$available" || "$available" -lt 2097152 ]]; then
            printf 'host-memory-headroom-below-2GiB\n' >"$PRIVATE/abort-reason.txt"
            if [[ -f "$PRIVATE/active-process.pid" ]]; then
                child=$(cat "$PRIVATE/active-process.pid")
                if [[ "$child" =~ ^[0-9]+$ && $(ps -o pgid= -p "$child" | tr -d ' ') == "$child" ]]; then
                    kill -TERM -- "-$child" 2>/dev/null || true
                fi
            fi
            stop_test_only
            kill -TERM "$MAIN_PID"
            return
        fi
        sleep 2
    done
}
memory_guard &
GUARD_PID=$!

# Read only: administrator secret remains inside the existing web container.
STAGE=postgres-preflight
status running
docker exec -i "$PROD_WEB" python - <<'PY' >"$PUBLIC/postgres-preflight.json"
import json, os, psycopg
try:
    with psycopg.connect(host='db',port=5432,dbname=os.environ['POSTGRES_DB'],
        user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],connect_timeout=5,
        options='-c statement_timeout=30000 -c lock_timeout=5000') as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT current_setting('server_version_num')::int, current_setting('max_connections')::int, (SELECT count(*) FROM pg_stat_activity), current_setting('shared_buffers'),current_setting('work_mem'),current_setting('maintenance_work_mem'),current_setting('fsync'),current_setting('synchronous_commit')")
            version, maximum, used, shared, work, maintenance, fsync, synchronous = cur.fetchone()
    assert 170000 <= version < 180000, 'PostgreSQL17 required'
    assert maximum-used >= 40, 'Insufficient PostgreSQL connection headroom'
    print(json.dumps({'postgres_version_num':version,'max_connections':maximum,'current_connections':used,'headroom':maximum-used,
        'shared_buffers':shared,'work_mem':work,'maintenance_work_mem':maintenance,'fsync':fsync,'synchronous_commit':synchronous}))
except Exception as error:
    raise SystemExit('PostgreSQL preflight failed: '+type(error).__name__) from None
PY
chmod 644 "$PUBLIC/postgres-preflight.json"
chown ktdata:ktdata "$PUBLIC/postgres-preflight.json"

# Refuse any existing name on first run. An owned empty DB was independently
# checked by the resume wrapper before any existing private state is reused.
if [[ -z "$RESUME_RUN_ID" ]]; then
STAGE=create-isolated-database
status running
docker exec -i "$PROD_WEB" python - "$RUN_ID" <<'PY' >"$PRIVATE/school-test.env"
import os, secrets, sys, psycopg
from psycopg import sql
try:
    conn=psycopg.connect(host='db',port=5432,dbname=os.environ['POSTGRES_DB'],
      user=os.environ['POSTGRES_USER'],password=os.environ['POSTGRES_PASSWORD'],connect_timeout=5,autocommit=True,
      options='-c statement_timeout=30000 -c lock_timeout=5000')
    password=secrets.token_urlsafe(48)
    with conn.cursor() as cur:
        cur.execute("SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname='kt_e2e') OR EXISTS(SELECT 1 FROM pg_roles WHERE rolname='kt_e2e_user')")
        if cur.fetchone()[0]: raise RuntimeError('Unknown or existing kt_e2e database/role; refusing reuse')
        cur.execute(sql.SQL('CREATE ROLE {} LOGIN PASSWORD {} CONNECTION LIMIT 48').format(sql.Identifier('kt_e2e_user'),sql.Literal(password)))
        cur.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier('kt_e2e'),sql.Identifier('kt_e2e_user')))
        cur.execute(sql.SQL('COMMENT ON DATABASE {} IS {}').format(sql.Identifier('kt_e2e'),sql.Literal('school-cache-candidate '+sys.argv[1])))
    conn.close()
    values={'SCHOOL_RUNTIME_ISOLATION_ENABLED':'1','POSTGRES_DB':'kt_e2e','POSTGRES_USER':'kt_e2e_user',
      'POSTGRES_PASSWORD':password,'POSTGRES_PORT':'5432','POSTGRES_HOST':'school-test-postgres',
      'SCHOOL_TEST_POSTGRES_USER':'kt_e2e_user','SCHOOL_TEST_POSTGRES_PASSWORD':password,
      'DJANGO_SECRET_KEY':secrets.token_urlsafe(64),'DJANGO_DEBUG':'0','SEED_DEMO':'0',
      'DJANGO_ALLOWED_HOSTS':'localhost,127.0.0.1','DJANGO_CSRF_TRUSTED_ORIGINS':'http://127.0.0.1:18080',
      'DJANGO_SESSION_COOKIE_SECURE':'0','DJANGO_CSRF_COOKIE_SECURE':'0',
      'CLASS_CENTER_ENABLED':'1','STUDENT_LEARNING_ANALYTICS_ENABLED':'1',
      'STUDENT_SHARED_RESULTS_ENABLED':'1','STUDENT_SHARED_RESULTS_COURSE_IDS':'*',
      'ADVANCED_DATA_MATERIALIZED_ENABLED':'1','ADVANCED_DATA_MATERIALIZED_COURSE_IDS':'*'}
    for key,value in values.items(): print(key+'='+value)
except Exception as error:
    # Do not format SQL errors, query text, or passwords into operator output.
    message='Unknown or existing kt_e2e database/role; refusing reuse' if isinstance(error,RuntimeError) else type(error).__name__
    raise SystemExit('Isolated database setup failed: '+message) from None
PY
chmod 600 "$PRIVATE/school-test.env"
ln -s "$PRIVATE/school-test.env" "$CANDIDATE/school-test.env"
fi
set -a
source "$PRIVATE/school-test.env"
set +a
COMPOSE=(docker compose -p "$PROJECT" --env-file "$PRIVATE/school-test.env" -f docker-compose.yml -f docker-compose.school.yml -f docker-compose.school-test.yml -f docker-compose.school-test-external-db.yml)
run_step compose-validation "${COMPOSE[@]}" config --quiet
if [[ ${SCHOOL_CACHED_BUILD:-0} == 1 ]]; then
    run_step cached-candidate-build python3 scripts/ops/school_cached_build.py build --run-id "$RUN_ID"
    COMPOSE+=(-f "$PRIVATE/cached-images.json")
    run_step cached-compose-validation "${COMPOSE[@]}" config --quiet
else
    run_step candidate-build "${COMPOSE[@]}" build
fi
ACTIVE_SERVICES=()
if [[ "$SHORT_ONLY" == 1 ]]; then ACTIVE_SERVICES=(web student-read teacher-read scheduler nginx); fi
run_step create-test-services "${COMPOSE[@]}" create --no-build "${ACTIVE_SERVICES[@]}"
STAGE=attach-test-postgres
status running
python3 - "${PROJECT}_default" "$PROD_DB" <<'PY' >"$PRIVATE/attach-test-postgres.log" 2>&1
import json,subprocess,sys
network,container=sys.argv[1:]
networks=json.loads(subprocess.check_output(['docker','inspect',container,'--format','{{json .NetworkSettings.Networks}}'],text=True))
if network in networks:
    assert 'school-test-postgres' in networks[network]['Aliases'], 'Existing test network alias mismatch'
else:
    subprocess.run(['docker','network','connect','--alias','school-test-postgres',network,container],check=True)
PY
run_step candidate-start "${COMPOSE[@]}" up -d --no-build --wait --wait-timeout 240 "${ACTIVE_SERVICES[@]}"
run_step nginx-parser-check "${COMPOSE[@]}" exec -T nginx nginx -t

# Verify physical DB and real processes for all three pools; no credentials printed.
for role in web student-read teacher-read; do
    STAGE="verify-$role"
    status running
    "${COMPOSE[@]}" exec -T "$role" python - "$role" <<'PY' >"$PUBLIC/runtime-$(echo "$role" | tr - _).json"
import json, os, pathlib, sys
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
import django; django.setup()
from django.db import connection
with connection.cursor() as cur:
    cur.execute('SELECT current_database()'); physical=cur.fetchone()[0]
count=0
for path in pathlib.Path('/proc').glob('[0-9]*/cmdline'):
    try:
        words=path.read_bytes().split(b'\0')[:2]
        count+=int(any(b'gunicorn' in word for word in words))
    except OSError: pass
configured_workers=int(os.environ['GUNICORN_WORKERS'])
assert physical=='kt_e2e' and configured_workers>0 and count==configured_workers+1
print(json.dumps({'physical_database':physical,'pool_alias':connection.settings_dict['NAME'],'role':sys.argv[1],
    'configured_workers':configured_workers,'gunicorn_processes_including_master':count}))
PY
done
run_step pgbouncer-pools "${COMPOSE[@]}" exec -T pgbouncer sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -A -t -c "SHOW DATABASES"'
python3 - "$PRIVATE/pgbouncer-pools.log" <<'PY'
import pathlib,sys
rows=pathlib.Path(sys.argv[1]).read_text().splitlines()
assert all(any(row.startswith(alias+'|') for row in rows) for alias in ('kt_core','kt_student','kt_teacher','kt_background'))
PY
run_step capacity-preflight "${COMPOSE[@]}" exec -T web python manage.py report_300_capacity
cp "$PRIVATE/capacity-preflight.log" "$PUBLIC/capacity.json"
WEB_IMAGE=$("${COMPOSE[@]}" images -q web | head -n1)
[[ -n "$WEB_IMAGE" ]] || exit 1
FIXTURE_RUN_ID=${SCHOOL_FIXTURE_RUN_ID:-$RUN_ID}
if [[ ${SCHOOL_REUSE_FIXTURES:-0} != 1 ]]; then
mkdir "$PRIVATE/fixtures"
python3 - "$PRIVATE/fixture-password" <<'PY'
import pathlib,secrets,sys
pathlib.Path(sys.argv[1]).write_text(secrets.token_urlsafe(32))
PY
run_step seed-fixtures "${COMPOSE[@]}" run --rm --no-deps -v "$PRIVATE:/private" -e POSTGRES_HOST=school-test-postgres -e POSTGRES_POOL_ALIAS= web python manage.py seed_school_mixed_load --run-id "$FIXTURE_RUN_ID" --password-file /private/fixture-password --output-dir /private/fixtures
else
    [[ -n "$RESUME_RUN_ID" && "$FIXTURE_RUN_ID" == "$RUN_ID-v6" ]] || exit 1
    for phase in baseline mixed; do
        [[ -f "$PRIVATE/fixtures/$FIXTURE_RUN_ID-$phase.json" && -f "$PRIVATE/fixtures/$FIXTURE_RUN_ID-$phase-history.json" ]] || exit 1
    done
fi

# Bind every short report to immutable images, actual role counts and the
# explicit PG budget. Never serialize environment variables or fixture secrets.
STAGE=short-config-binding
status running
python3 scripts/ops/school_scheduler_diagnostic.py >>"$PRIVATE/$STAGE.log" 2>&1
python3 - "$PRIVATE" "$PUBLIC" "$PROJECT" "$SHORT_ONLY" <<'PY'
import hashlib,json,pathlib,subprocess,sys
private,public=map(pathlib.Path,sys.argv[1:3]); project=sys.argv[3]; short_only=sys.argv[4]=='1'
binding=json.loads((private/'cached-source-binding.json').read_text())
sys.path.insert(0,str(pathlib.Path('scripts/load').resolve()))
from observe_school_runtime import sample_container, required_service, is_oneoff
pg=json.loads(subprocess.check_output(['docker','inspect','kt_plantform-db-1'],text=True))[0]
pg_sample=sample_container(pg,required=True); pg_kernel=pg_sample['metrics']
ids=subprocess.check_output(['docker','ps','-q','--filter','label=com.docker.compose.project='+project],text=True).split()
containers=json.loads(subprocess.check_output(['docker','inspect',*ids],text=True))
roles={}; lifecycle=[pg_sample['lifecycle']]
required={'web','student-read','teacher-read','scheduler','nginx','pgbouncer'}
for item in containers:
 role=item['Config']['Labels']['com.docker.compose.service']
 sample=sample_container(item,required=required_service(item,project,required)); lifecycle.append(sample['lifecycle'])
 if sample['metrics'] is None: continue
 item=sample['container']
 if is_oneoff(item): continue
 environment=dict(value.split('=',1) for value in item['Config']['Env'] if '=' in value)
 row={'actual_running':True,'image_id':item['Image'],'cpus':item['HostConfig']['NanoCpus']/1e9,'memory_bytes':item['HostConfig']['Memory'],'cgroup':sample['metrics']}
 if role in ('web','student-read','teacher-read'): row['workers']=int(environment['GUNICORN_WORKERS'])
 else: row['replicas']=1
 assert role not in roles
 roles[role]=row
assert required <= roles.keys()
assert [roles[name]['workers'] for name in ('web','student-read','teacher-read')]==[12,2,1]
if short_only:
 assert not set(roles)&{'analytics-worker','student-comparison-worker','publication-worker','export-worker'}
 assert roles['web']['cpus']==0
 assert all(roles[name]['cpus']==0 for name in ('web','nginx','pgbouncer'))
 assert all(roles[name]['cgroup']['cpu_max'].startswith('max ') for name in ('web','nginx','pgbouncer'))
 assert pg_kernel['cpu_max'].startswith('max ')
 for role in ('analytics-worker','student-comparison-worker','publication-worker','export-worker'): roles[role]={'actual_running':False,'configured_replicas':1}
else: assert roles['analytics-worker']['replicas']==1
config={'run_id':binding['run_id'],'source_sha256':binding['candidate_archive_sha256'],
 'frontend_sha256':binding['frontend_archive_sha256'],'controller_sha256':hashlib.sha256(pathlib.Path('scripts/ops/school_capture_priority.py').read_bytes()).hexdigest(),
 'roles':roles,'scope':'fixed_short_capture' if short_only else 'formal_school_acceptance',
 'dynamic_cpu_control':False,'shared_postgres_cgroup':pg_kernel,'shared_postgres_read_only':True,
 'sampling_lifecycle':lifecycle,
 'cpu_capacity_shared_with_production':True,
 'actual_postgres_settings':json.loads((public/'postgres-preflight.json').read_text())}
config['config_sha256']=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
(public/'source-config.json').write_text(json.dumps(config,indent=2))
PY
run_step seed-short-fixtures "${COMPOSE[@]}" run --rm --no-deps -v "$PRIVATE:/private" -v "$PUBLIC:/reports" -e POSTGRES_HOST=school-test-postgres -e POSTGRES_POOL_ALIAS= web python manage.py seed_school_short_load --manifest "/private/fixtures/$FIXTURE_RUN_ID-baseline.json" --output-dir /private/shorts --source-config /reports/source-config.json

run_load_step() {
    local stage=$1
    local name=$2
    shift 2
    run_step "$stage" docker run --rm --network host --memory 512m --cpus 1 --label "com.docker.compose.project=$PROJECT" --name "$PROJECT-load-$name" -v "$PRIVATE:/private:ro" -v "$PUBLIC:/reports" --entrypoint python "$WEB_IMAGE" "$@"
}
start_observer() {
    local name=$1
    STAGE="$name-observation"
    status running
    [[ ! -e "$PRIVATE/$name-observer.stop" ]] || exit 1
    setsid python3 scripts/load/observe_school_runtime.py --project "$PROJECT" --database-container "$PROD_DB" --duration 2700 --stop-file "$PRIVATE/$name-observer.stop" --abort-memory-below-mb 2048 --output "$PUBLIC/$name-observation.json" >"$PRIVATE/$name-observation.log" 2>&1 &
    OBSERVER_PID=$!
    sleep 2
    kill -0 "$OBSERVER_PID"
    [[ -z "$PRESSURE_PID" ]] || kill -0 "$PRESSURE_PID"
}
complete_observer() {
    local name=$1
    touch "$PRIVATE/$name-observer.stop"
    wait "$OBSERVER_PID"
    OBSERVER_PID=
    [[ -z "$PRESSURE_PID" ]] || kill -0 "$PRESSURE_PID"
}
start_observer short
SHORT_COUNTS=(3 50 150 300)

for count in "${SHORT_COUNTS[@]}"; do
    run_step "short-$count-http" docker run --rm --network host --memory 512m --cpu-shares 256 --label "com.docker.compose.project=$PROJECT" --name "$PROJECT-load-short-$count" -v "$PRIVATE:/private" -v "$PUBLIC:/reports" --entrypoint python "$WEB_IMAGE" scripts/load/school_short_storm.py --base-url http://127.0.0.1:18080 --manifest "/private/shorts/short-$count.json" --output "/reports/short-$count.json" --timeout 35
    run_step "short-$count-persistence" "${COMPOSE[@]}" run --rm --no-deps -v "$PRIVATE:/private:ro" -v "$PUBLIC:/reports" -e POSTGRES_HOST=school-test-postgres -e POSTGRES_POOL_ALIAS= web python manage.py verify_school_short_load --manifest "/private/shorts/short-$count.json" --output "/reports/short-$count-persistence.json" --cold-manifest "/private/fixtures/$FIXTURE_RUN_ID-baseline.json" --cold-manifest "/private/fixtures/$FIXTURE_RUN_ID-mixed.json"
done
complete_observer short
python3 - "$PUBLIC" "$SHORT_ONLY" <<'PY'
import json,pathlib,sys
public=pathlib.Path(sys.argv[1]); rows={}
config=json.loads((public/'source-config.json').read_text())
for n in (3,50,150,300):
 values=[json.loads((public/name).read_text()) for name in (f'short-{n}.json',f'short-{n}-persistence.json')]
 assert all(value['student_count']==n and value['source_config']==config and value['checks'] and all(v is True for v in value['checks'].values()) and value['passed'] is True for value in values)
 rows[str(n)]={'http_file':f'short-{n}.json','persistence_file':f'short-{n}-persistence.json','http_checks':values[0]['checks'],'persistence_checks':values[1]['checks']}
observation=json.loads((public/'short-observation.json').read_text())
assert observation['oom_kills']==0 and observation['swap_end_bytes']<=observation['swap_start_bytes']
assert observation['peak_memory_budget']['checks'] and all(observation['peak_memory_budget']['checks'].values())
assert observation['host_database_samples'] and all(row['memory_budget']['checks']['host_headroom'] for row in observation['host_database_samples'])
(public/'short-acceptance.json').write_text(json.dumps({'passed':True,'formal_capacity_accepted':False,'source_config':config,'phases':rows},indent=2))
PY
if [[ "$SHORT_ONLY" == 1 ]]; then
    STAGE=short-completed
    stop_test_only
    status short-completed
    exit 0
fi
for phase in baseline mixed; do
    run_load_step "$phase-history-http" "$phase-history" scripts/load/assessment_300.py --fixture-preparation --base-url http://127.0.0.1:18080 --manifest "/private/fixtures/$FIXTURE_RUN_ID-$phase-history.json" --capacity-report /reports/capacity.json --output "/reports/$phase-history.json"
done

drain() {
    local target=$1
    local tries=0
    STAGE=$target
    status running
    while [[ $tries -lt 180 ]]; do
        docker exec -i "$PROD_DB" sh -c 'exec psql -X -t -A -U "$POSTGRES_USER" -d kt_e2e' <<'SQL' >"$PUBLIC/$target.json"
SELECT json_build_object(
 'research_pending',(SELECT count(*) FROM analytics_researchsessionmaterialization WHERE status IN ('queued','running')),
 'research_failed',(SELECT count(*) FROM analytics_researchsessionmaterialization WHERE status='failed'),
 'research_missing',(SELECT count(*) FROM assessment_experimentsession s LEFT JOIN analytics_researchsessionmaterialization m ON m.session_id=s.id AND m.is_current AND m.status='succeeded' AND m.source_revision=s.research_source_revision WHERE s.terminal_at IS NOT NULL AND m.materialization_id IS NULL),
 'comparison_pending',(SELECT count(*) FROM experiments_classexperimenttask t LEFT JOIN analytics_studentclasstaskcomparison c ON c.class_task_id=t.id WHERE t.result_revision>0 AND (c.id IS NULL OR c.status<>'ready' OR c.source_revision<>t.result_revision)),
 'export_pending',(SELECT count(*) FROM analytics_exportjob WHERE status IN ('queued','running')),
 'export_failed',(SELECT count(*) FROM analytics_exportjob WHERE status='failed'));
SQL
        if python3 - "$PUBLIC/$target.json" <<'PY'
import json,sys
raise SystemExit(0 if all(value==0 for value in json.load(open(sys.argv[1])).values()) else 1)
PY
        then return; fi
        tries=$((tries+1))
        sleep 5
    done
    return 1
}
drain history-drain
start_observer baseline
run_load_step baseline-30-minute baseline scripts/load/school_mixed_300.py --phase baseline --base-url http://127.0.0.1:18080 --manifest "/private/fixtures/$FIXTURE_RUN_ID-baseline.json" --duration 1800 --output /reports/baseline.json
drain baseline-drain
complete_observer baseline
start_observer mixed
# Missing observation intentionally returns 1; final evaluation follows observer completion.
run_load_step mixed-30-minute mixed scripts/load/school_mixed_300.py --phase mixed --base-url http://127.0.0.1:18080 --manifest "/private/fixtures/$FIXTURE_RUN_ID-mixed.json" --duration 1800 --baseline-result /reports/baseline.json --output /reports/mixed.json || [[ $? == 1 && -s "$PUBLIC/mixed.json" ]]
STAGE=observation-completion
status running
drain mixed-drain
complete_observer mixed
cp "$PUBLIC/mixed-observation.json" "$PUBLIC/observation.json"
run_load_step formal-acceptance evaluate scripts/load/evaluate_school_load.py --baseline /reports/baseline.json --mixed /reports/mixed.json --baseline-observation /reports/baseline-observation.json --source-config /reports/source-config.json --observation /reports/observation.json --output /reports/acceptance.json
STAGE=accepted-candidate-no-promotion
chmod 644 "$PUBLIC"/*.json
chown ktdata:ktdata "$PUBLIC"/*.json
status passed
# Deliberately retain test DB, candidate images and evidence for review. Production
# source/images/cutover and any derived-job repair are independent later steps.
