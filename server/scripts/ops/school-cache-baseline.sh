#!/usr/bin/env bash
set -euo pipefail
cd /data/kt-platform/KT_Plantform
printf 'SCHOOL_CACHE_BASELINE\n'
date -u
free -m
df -h /data
docker compose -p kt_plantform ps --all
docker stats --no-stream --format '{{.Name}} CPU={{.CPUPerc}} MEM={{.MemUsage}} LIMIT={{.MemPerc}}'
for container in $(docker compose -p kt_plantform ps -aq); do
  docker inspect --format '{{.Name}} Memory={{.HostConfig.Memory}} NanoCpus={{.HostConfig.NanoCpus}} OOM={{.State.OOMKilled}} Restarts={{.RestartCount}}' "$container"
done
docker compose -p kt_plantform exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT version();
SELECT datname, state, wait_event_type, wait_event, count(*) FROM pg_stat_activity GROUP BY 1,2,3,4;
SELECT status, count(*), min(created_at) FROM analytics_researchsessionmaterialization GROUP BY status;
SELECT count(*) AS terminal_sessions FROM assessment_experimentsession WHERE status IN ('completed','expired');
SQL
printf 'BASELINE_COMPLETE\n'
