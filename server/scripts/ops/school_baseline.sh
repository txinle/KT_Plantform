#!/usr/bin/env sh
# Read-only baseline; excludes environment values, passwords and query bodies.
set -eu
project=${1:?Pass the existing school Compose project name}
date -u +%FT%TZ
uname -r
free -m
df -h /
docker version --format '{{.Server.Version}}'
docker compose version
docker ps --filter "label=com.docker.compose.project=$project" --format '{{.Names}} {{.Image}} {{.Status}} {{.Ports}}'
ids=$(docker ps -q --filter "label=com.docker.compose.project=$project")
[ -n "$ids" ] || exit 1
# Fixed inspect fields intentionally omit Config.Env and credentials.
docker inspect --format '{{.Name}} image={{.Image}} memory={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}} restart={{.RestartCount}} oom={{.State.OOMKilled}}' $ids
docker stats --no-stream --format '{{.Name}} {{.MemUsage}} {{.CPUPerc}} {{.PIDs}}' $ids
