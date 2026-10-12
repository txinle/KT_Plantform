#!/usr/bin/env bash
# Production deployment was authorized. Formal candidate evidence is mandatory.
# Restore old routes/images on failure, retaining migrations and new submissions.
set -Eeuo pipefail
umask 077
[[ $(id -u) == 0 && $# == 3 ]] || {
    echo 'Usage (sudo): school_production_promote.sh SOURCE_ARCHIVE SOURCE_SHA256 ACCEPTED_REPORT_DIRECTORY' >&2
    exit 1
}
ARCHIVE=$(readlink -f "$1")
SHA256=$2
REPORT=$(readlink -f "$3")
[[ "$SHA256" =~ ^[a-f0-9]{64}$ && "$REPORT" =~ ^/home/ktdata/school-cache-reports/[0-9]{8}T[0-9]{6}Z-[0-9]+$ ]] || exit 1
PRODUCTION=/data/kt-platform/KT_Plantform
CANDIDATE=/home/ktdata/school-cache-candidate
SUPPORT=$(dirname "$(readlink -f "$0")")/school_release_support.py
PROBE=$(dirname "$SUPPORT")/school_teacher_probe.sh
STUDENT_PROBE=$(dirname "$SUPPORT")/school_student_probe.sh
PROJECT=kt_plantform
TEST_PROJECT=kt-school-e2e
PROD_DB=kt_plantform-db-1
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
PRIVATE=/home/ktdata/school-promotion-private/$RUN_ID
PUBLIC=/home/ktdata/school-promotion-reports/$RUN_ID
RELEASE=/data/kt-platform/school-releases/$RUN_ID
STAGE=preflight
CUTOVER_STARTED=0
ROLLBACK_STATE=not-needed
GUARD_PID=
CONTROLLER_STARTED=0
MAIN_PID=$$
[[ $(readlink -f "$PRODUCTION") == "$PRODUCTION" && -f "$PRODUCTION/.env" ]] || exit 1
command -v flock >/dev/null
command -v timeout >/dev/null
exec 9>/run/lock/kt-school-production-promote.lock
flock -n 9 || { echo 'Another school promotion is active.' >&2; exit 1; }
mkdir -p /home/ktdata/school-promotion-private /home/ktdata/school-promotion-reports /data/kt-platform/school-releases
mkdir "$PRIVATE" "$PUBLIC"
chmod 700 "$PRIVATE"
chmod 755 /home/ktdata/school-promotion-reports "$PUBLIC"
chown ktdata:ktdata /home/ktdata/school-promotion-reports "$PUBLIC"

status() {
    python3 - "$PUBLIC/status.json" "$1" "$STAGE" "${2:-0}" "$CUTOVER_STARTED" "$ROLLBACK_STATE" "$RUN_ID" "$CONTROLLER_STARTED" <<'PY'
import datetime,json,os,sys
path,state,stage,code,cutover,rollback,run,controller=sys.argv[1:]
body={'state':state,'stage':stage,'exit_code':int(code),'cutover_started':cutover=='1',
      'rollback':rollback,'run_id':run,'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),
      'database_restored':False,'new_submissions_retained':True,
      'auto_pressure_controller_deployed':controller=='1'}
with open(path+'.tmp','w') as out: json.dump(body,out,indent=2)
os.replace(path+'.tmp',path)
PY
    chmod 644 "$PUBLIC/status.json"
    chown ktdata:ktdata "$PUBLIC/status.json"
    printf '%s %s\n' "$1" "$STAGE"
}

step() {
    STAGE=$1
    shift
    status running
    setsid "$@" <&0 >"$PRIVATE/$STAGE.log" 2>&1 &
    local child=$!
    printf '%s\n' "$child" >"$PRIVATE/active-process.pid"
    local code=0
    wait "$child" || code=$?
    rm -f "$PRIVATE/active-process.pid"
    return "$code"
}

verify_accepted_postgres_resources() {
    python3 - "$PRIVATE/facts.json" "$PROD_DB" <<'PY'
import json,pathlib,subprocess,sys
facts=json.load(open(sys.argv[1]))
item=json.loads(subprocess.check_output(['docker','inspect',sys.argv[2]],text=True))[0]
assert item['Id']==facts['production_database_container'] and item['State']['Running'] and item['State']['Pid']>1
labels=item['Config']['Labels']
assert labels.get('com.docker.compose.project')=='kt_plantform' and labels.get('com.docker.compose.service')=='db'
relative=pathlib.Path('/proc',str(item['State']['Pid']),'cgroup').read_text().strip().split('0::',1)[1]
group=(pathlib.Path('/sys/fs/cgroup')/relative.lstrip('/')).resolve()
assert group.is_relative_to(pathlib.Path('/sys/fs/cgroup')) and item['Id'] in str(group)
actual={name:(group/name).read_text().strip() for name in facts['accepted_postgres_kernel_limits']}
assert actual==facts['accepted_postgres_kernel_limits']
PY
}

stop_owned_pressure_service() {
    local unit=/etc/systemd/system/kt-school-pressure.service
    [[ -e "$unit" || -L "$unit" ]] || return 0
    local report was_active=0
    report=$(python3 "$SUPPORT" --verify-pressure-unit "$unit") || return 1
    systemctl is-active --quiet kt-school-pressure.service && was_active=1
    systemctl stop kt-school-pressure.service || return 1
    if [[ $was_active == 1 || -f "$report" ]]; then
        python3 - "$report" <<'PY' || return 1
import json,sys
value=json.load(open(sys.argv[1]))
roles={'web','student-read','teacher-read','analytics-worker','student-comparison-worker','export-worker'}
assert value['state'] in ('stopped-restored','failed') and value['mode']=='normal'
assert set(value['restored'])==roles and all(v is True for v in value['restored'].values())
PY
    fi
    CONTROLLER_STARTED=0
}


rollback() {
    STAGE=restore-old-routes-and-images
    ROLLBACK_STATE=running
    status running
    if ! stop_owned_pressure_service >>"$PRIVATE/rollback.log" 2>&1; then
        ROLLBACK_STATE=failed-manual-recovery-required
        return 1
    fi
    if [[ -f /etc/systemd/system/kt-school-pressure.service ]]; then
        systemctl disable kt-school-pressure.service >>"$PRIVATE/rollback.log" 2>&1 || true
    fi
    # Stop only newly introduced roles; old config supplies exact prior counts.
    for role in student-read teacher-read student-comparison-worker export-worker analytics-worker; do
        local ids
        ids=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter "label=com.docker.compose.service=$role")
        [[ -z "$ids" ]] || docker stop --time 15 $ids >>"$PRIVATE/rollback.log" 2>&1 || true
    done
    local old_services=()
    mapfile -t old_services < <(python3 - "$PRIVATE/facts.json" <<'PY'
import json,sys
for service,count in json.load(open(sys.argv[1]))['old_running_counts'].items():
    if service not in ('db','migrate') and count: print(service)
PY
)
    if timeout 180 docker compose --project-directory "$PRODUCTION" -p "$PROJECT" -f "$PRIVATE/rollback-compose.json" up -d --no-deps --no-build --force-recreate --wait --wait-timeout 150 "${old_services[@]}" >>"$PRIVATE/rollback.log" 2>&1; then
        ROLLBACK_STATE=restored-old-services
    else
        ROLLBACK_STATE=failed-manual-recovery-required
    fi
    # Shared PostgreSQL remains unchanged; verify its observed profile only.
    if ! verify_accepted_postgres_resources >>"$PRIVATE/rollback.log" 2>&1; then
        ROLLBACK_STATE=failed-manual-recovery-required
    fi
    if [[ -f "$PRIVATE/prior-kt-school-pressure.service" ]]; then
        if ! install -o root -g root -m 644 "$PRIVATE/prior-kt-school-pressure.service" /etc/systemd/system/kt-school-pressure.service || ! systemctl daemon-reload; then
            ROLLBACK_STATE=failed-manual-recovery-required
        fi
        # Old application code remains restored with its controller stopped.
    fi
}

finish() {
    local code=$?
    trap - EXIT TERM INT
    [[ -z "$GUARD_PID" ]] || kill "$GUARD_PID" 2>/dev/null || true
    if [[ $code -ne 0 ]]; then
        if [[ $CUTOVER_STARTED == 1 ]]; then rollback || true; fi
        status failed "$code"
    fi
    exit "$code"
}
trap finish EXIT
trap 'exit 130' TERM INT

memory_guard() {
    while kill -0 "$MAIN_PID" 2>/dev/null; do
        if [[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -lt 2097152 ]]; then
            printf 'host-memory-headroom-below-2GiB\n' >"$PRIVATE/abort-reason.txt"
            if [[ -f "$PRIVATE/active-process.pid" ]]; then
                local child
                child=$(cat "$PRIVATE/active-process.pid")
                if [[ "$child" =~ ^[0-9]+$ && $(ps -o pgid= -p "$child" | tr -d ' ') == "$child" ]]; then
                    kill -TERM -- "-$child" 2>/dev/null || true
                fi
            fi
            kill -TERM "$MAIN_PID"
            return
        fi
        sleep 2
    done
}
memory_guard &
GUARD_PID=$!

step verify-acceptance-and-source python3 "$SUPPORT" --private "$PRIVATE" --candidate "$CANDIDATE" --release "$RELEASE" --report "$REPORT" --archive "$ARCHIVE" --sha256 "$SHA256"
# Scope selection is a POST followed by the preview GET in the actual UI.
# Require the independent HTTP measurement from the same timed mixed phase.
step teacher-scope-to-preview-acceptance python3 - "$REPORT" "$SHA256" <<'PY'
import json,math,pathlib,sys
report=pathlib.Path(sys.argv[1])
value=json.loads((report/'teacher-scope-preview.json').read_text())
mixed=json.loads((report/'mixed.json').read_text())
assert value['passed'] and value['state']=='completed' and value['source_sha256']==sys.argv[2]
assert value['run_id']==report.name
assert value['checks']['history_ready_before_measurement']
assert value['authentication_completed_at']<=mixed['started_at']
rows=value['samples']
assert len(rows)>=25 and all(row['started_at']>=mixed['started_at'] and row['ended_at']<=mixed['ended_at'] for row in rows)
successful=[row for row in rows if row['ok']]
assert (len(rows)-len(successful))/len(rows)<.001
seconds=sorted(row['seconds'] for row in successful)
assert seconds and seconds[math.ceil(len(seconds)*.95)-1]<=1.5
assert all({row['scope_slot'] for row in successful if row['teacher_slot']==teacher}==set(range(5)) for teacher in range(5))
print('actual-teacher-scope-post-to-first-preview-gate-confirmed')
PY
step safe-old-code-rollback-flags python3 - "$PRIVATE/rollback-compose.json" <<'PY'
import json,os,pathlib,sys
path=pathlib.Path(sys.argv[1])
config=json.loads(path.read_text())
for service in ('web','scheduler','analytics-worker','publication-worker'):
    if service in config['services']:
        environment=config['services'][service].setdefault('environment',{})
        environment.update(ADVANCED_DATA_MATERIALIZED_ENABLED='0',STUDENT_SHARED_RESULTS_ENABLED='0')
temporary=path.with_suffix('.tmp')
temporary.write_text(json.dumps(config,indent=2));temporary.chmod(0o600)
os.replace(temporary,path)
print('legacy-rollback-reader-kill-switches-confirmed')
PY
NETWORK=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["production_network"])' "$PRIVATE/facts.json")
WEB_IMAGE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["accepted_images"]["web"])' "$PRIVATE/facts.json")
NGINX_IMAGE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["accepted_images"]["nginx"])' "$PRIVATE/facts.json")
export SCHOOL_RUNTIME_ISOLATION_ENABLED=1 SCHOOL_HTTP_PORT=8080 SCHOOL_STUDENT_SHARED_RESULTS_COURSE_IDS=2
COMPOSE=(docker compose --project-directory "$PRODUCTION" -p "$PROJECT" --env-file "$RELEASE/.env" -f "$RELEASE/docker-compose.yml" -f "$RELEASE/docker-compose.school.yml" -f "$RELEASE/docker-compose.production.json")
step production-config-validation "${COMPOSE[@]}" config --quiet

# Stop accepted test HTTP/worker processes to reclaim resources, keeping its
# database, images, evidence, network and production container untouched.
STAGE=stop-accepted-test-workload
status running
TEST_IDS=$(docker ps -q --filter "label=com.docker.compose.project=$TEST_PROJECT")
[[ -z "$TEST_IDS" ]] || timeout 90 docker stop --time 30 $TEST_IDS >"$PRIVATE/stop-test.log" 2>&1
[[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -ge 8388608 ]] || { echo 'Production preparation requires 8GiB MemAvailable.' >&2; exit 1; }
OLD_WEB=$(docker ps -q --filter "label=com.docker.compose.project=$PROJECT" --filter label=com.docker.compose.service=web)
[[ -n "$OLD_WEB" && "$OLD_WEB" != *$'\n'* ]] || exit 1
# A failing legacy endpoint is still useful baseline evidence; retain it, then
# judge the candidate using its independent formal acceptance and after probe.
step legacy-teacher-baseline bash "$PROBE" baseline "$OLD_WEB" "$PRIVATE/teacher-selection.json" "$PUBLIC/teacher-baseline.json" || [[ $? == 1 && -s "$PRIVATE/teacher-selection.json" && -s "$PUBLIC/teacher-baseline.json" ]]
STAGE=backup-disk-headroom
status running
DB_BYTES=$(docker exec "$PROD_DB" sh -c 'exec psql -X -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "SELECT pg_database_size(current_database())"')
python3 - "$PRIVATE" "$PRIVATE/facts.json" "$DB_BYTES" "$PRODUCTION" <<'PY'
import json,os,subprocess,sys
private,facts_path,db_bytes,production=sys.argv[1:]
images=set(json.load(open(facts_path))['old_images'].values())
image_bytes=sum(int(subprocess.check_output(['docker','image','inspect',image,'--format','{{.Size}}'],text=True)) for image in images)
source_bytes=int(subprocess.check_output(['du','-sb',production],text=True).split()[0])
space=os.statvfs(private)
assert space.f_bavail*space.f_frsize >= 2*int(db_bytes)+image_bytes+source_bytes, 'Insufficient backup disk headroom'
PY
step backup-source tar --exclude=.venv --exclude=frontend/node_modules --exclude=media --exclude=.git -czf "$PRIVATE/production-source.tar.gz" -C "$PRODUCTION" .
step backup-old-images bash -c 'python3 - "$1" <<'"'"'PY'"'"' | xargs docker image save -o "$2"
import json,sys
for image in set(json.load(open(sys.argv[1]))["old_images"].values()): print(image)
PY' bash "$PRIVATE/facts.json" "$PRIVATE/old-images.tar"
step backup-database bash -o pipefail -c 'docker exec "$1" sh -c '\''exec pg_dump -Fc -U "$POSTGRES_USER" -d "$POSTGRES_DB"'\'' >"$2"' bash "$PROD_DB" "$PRIVATE/production.dump"
[[ -s "$PRIVATE/production.dump" && -s "$PRIVATE/old-images.tar" ]] || exit 1

# Existing PG stays online. A connection ceiling of at least64 must already be
# active; this script never restarts PG or applies ALTER SYSTEM.
STAGE=production-database-check
status running
docker exec -i "$PROD_DB" sh -c 'exec psql -X -t -A -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL' >"$PUBLIC/postgres-check.json"
SELECT json_build_object('database',current_database(),'max_connections',current_setting('max_connections')::int,'connections',(SELECT count(*) FROM pg_stat_activity));
SQL
python3 - "$PUBLIC/postgres-check.json" <<'PY'
import json,sys
row=json.load(open(sys.argv[1]))
assert row['database']!='kt_e2e' and row['max_connections']>=64 and row['max_connections']-row['connections']>=40
PY
chmod 644 "$PUBLIC/postgres-check.json"
chown ktdata:ktdata "$PUBLIC/postgres-check.json"
step nginx-image-parser timeout 60 docker run --rm --pull=never --network none --add-host web:127.0.0.1 --add-host student-read:127.0.0.1 --add-host teacher-read:127.0.0.1 --entrypoint nginx "$NGINX_IMAGE" -t
step migration-plan timeout 120 docker run --rm --pull=never --network "$NETWORK" --env-file "$RELEASE/.env" -e POSTGRES_HOST=db -e POSTGRES_PORT=5432 -e POSTGRES_POOL_ALIAS= --entrypoint python "$WEB_IMAGE" manage.py migrate --plan
step incremental-production-migrations timeout 1200 docker run --rm --pull=never --memory 768m --cpus 1 --network "$NETWORK" --env-file "$RELEASE/.env" -e POSTGRES_HOST=db -e POSTGRES_PORT=5432 -e POSTGRES_POOL_ALIAS= --entrypoint python "$WEB_IMAGE" manage.py migrate --noinput

[[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -ge 8388608 ]] || exit 1
STAGE=bounded-production-cutover
STAGE=stop-owned-production-pressure-controller
status running
stop_owned_pressure_service
CUTOVER_STARTED=1
status running
# Adjust cgroup limits only, preserving the live PostgreSQL process and volume.
verify_accepted_postgres_resources >"$PRIVATE/db-resource-verification.log" 2>&1
step bounded-production-cutover timeout 240 "${COMPOSE[@]}" up -d --no-deps --no-build --force-recreate --wait --wait-timeout 210 pgbouncer web student-read teacher-read scheduler analytics-worker student-comparison-worker publication-worker export-worker nginx
step nginx-running-parser "${COMPOSE[@]}" exec -T nginx nginx -t
step production-health python3 - <<'PY'
import json,urllib.request
request=urllib.request.Request('http://127.0.0.1:8080/health/',headers={'Host':'ktedu.online','X-Forwarded-Proto':'https'})
with urllib.request.urlopen(request,timeout=10) as response:
 assert response.status==200 and json.load(response)['status']=='ok'
print('production-health-ok')
PY
step production-pgbouncer-aliases "${COMPOSE[@]}" exec -T pgbouncer sh -c 'PGPASSWORD="$POSTGRES_PASSWORD" psql -X -h 127.0.0.1 -p 5432 -U "$POSTGRES_USER" -d pgbouncer -A -t -c "SHOW DATABASES"'
python3 - "$PRIVATE/production-pgbouncer-aliases.log" <<'PY'
import pathlib,sys
rows=pathlib.Path(sys.argv[1]).read_text().splitlines()
assert all(any(row.startswith(alias+'|') for row in rows) for alias in ('kt_core','kt_student','kt_teacher','kt_background'))
PY
step course-two-runtime-flags "${COMPOSE[@]}" exec -T web python - <<'PY'
import os
os.environ.setdefault('DJANGO_SETTINGS_MODULE','config.settings')
from config import settings
assert settings.ADVANCED_DATA_MATERIALIZED_ENABLED and settings.ADVANCED_DATA_MATERIALIZED_COURSE_IDS==frozenset({'2'})
assert settings.STUDENT_SHARED_RESULTS_ENABLED and settings.STUDENT_SHARED_RESULTS_COURSE_IDS==frozenset({'2'})
assert settings.SESSION_COOKIE_SECURE and settings.CSRF_COOKIE_SECURE
from apps.analytics.research_materialization import PROJECTION_VERSION
assert PROJECTION_VERSION=='research-session-v4'
print('course-two-flags-and-v4-confirmed')
PY
NEW_TEACHER=$("${COMPOSE[@]}" ps -q teacher-read)
STAGE=historical-v4-readiness-and-teacher-check
status running
ready=0
deadline=$((SECONDS+600))
while [[ $SECONDS -lt $deadline ]]; do
    if timeout 50 bash "$PROBE" after "$NEW_TEACHER" "$PRIVATE/teacher-selection.json" "$PUBLIC/teacher-after.json" >"$PRIVATE/teacher-after.log" 2>&1; then ready=1; break; fi
    [[ $(awk '/MemAvailable:/ {print $2}' /proc/meminfo) -ge 2097152 ]] || exit 1
    sleep 5
done
[[ $ready == 1 ]] || exit 1
NEW_STUDENT=$("${COMPOSE[@]}" ps -q student-read)
# The diagnostic keeps real authorization/feedback/cache code and rolls back
# all selected-user side effects. No eligible released result is a UAT gap,
# rather than a reason to override policy or undo an otherwise healthy release.
step student-api-with-rolled-back-effects bash "$STUDENT_PROBE" "$NEW_STUDENT" "$PRIVATE/student-selection.json" "$PUBLIC/student-after.json" || [[ $? == 2 ]]
STAGE=deployed-teacher-paths-verified
status passed
# Existing facts/frozen metadata, old derived versions and student submissions
# are retained. Student diagnostic side effects are rolled back.
