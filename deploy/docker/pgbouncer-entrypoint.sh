#!/usr/bin/env sh
set -eu
[ "${SCHOOL_RUNTIME_ISOLATION_ENABLED:-0}" = "1" ] || exit 1
: "${POSTGRES_DB:?}" "${POSTGRES_USER:?}" "${POSTGRES_PASSWORD:?}"
PGBOUNCER_SERVER_HOST=${PGBOUNCER_SERVER_HOST:-db}
# Connection identifiers are supplied by operators; reject template metacharacters.
case "$POSTGRES_DB:$POSTGRES_USER" in *[!a-zA-Z0-9_:]*) echo "Invalid database identifier" >&2; exit 1;; esac
case "$PGBOUNCER_SERVER_HOST" in *[!a-zA-Z0-9_.-]*) echo "Invalid database host" >&2; exit 1;; esac
umask 077
sed -e "s/\${POSTGRES_DB}/$POSTGRES_DB/g" -e "s/\${POSTGRES_USER}/$POSTGRES_USER/g" -e "s/\${PGBOUNCER_SERVER_HOST}/$PGBOUNCER_SERVER_HOST/g" /school/pgbouncer.ini.template > /tmp/pgbouncer-school.ini
escaped_password=$(printf '%s' "$POSTGRES_PASSWORD" | sed 's/\\/\\\\/g; s/"/\\"/g')
printf '"%s" "%s"\n' "$POSTGRES_USER" "$escaped_password" > /tmp/pgbouncer-school-users.txt
# edoburu runs as postgres; no root-only system path is written here.
exec pgbouncer /tmp/pgbouncer-school.ini
