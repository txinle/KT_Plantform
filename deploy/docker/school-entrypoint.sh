#!/usr/bin/env sh
set -eu
if [ "${SCHOOL_RUNTIME_ISOLATION_ENABLED:-0}" != "1" ]; then
    echo "School runtime requires explicit SCHOOL_RUNTIME_ISOLATION_ENABLED=1" >&2
    exit 1
fi
exec "$@"
