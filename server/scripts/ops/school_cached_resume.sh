#!/usr/bin/env bash
# Resume only the owned, empty, failed-before-build isolated test run.
set -Eeuo pipefail
umask 077
[[ $(id -u) == 0 && ( $# == 5 || ( $# == 6 && "$6" == --allow-owned-test-reseed-upgrade ) ) ]] || {
    echo 'Usage (sudo): school_cached_resume.sh RUN_ID SOURCE_ARCHIVE SOURCE_SHA256 FRONTEND_ARCHIVE FRONTEND_SHA256' >&2
    exit 1
}
cd /home/ktdata/school-cache-candidate
UPGRADE=()
if [[ $# == 6 ]]; then UPGRADE=(--allow-owned-test-reseed-upgrade); fi
python3 scripts/ops/school_cached_build.py verify-resume --run-id "$1" --archive "$2" --sha256 "$3" --frontend-archive "$4" --frontend-sha256 "$5" "${UPGRADE[@]}"
export SCHOOL_RESUME_RUN_ID="$1" SCHOOL_CACHED_BUILD=1
if [[ $# == 6 ]]; then export SCHOOL_FIXTURE_RUN_ID="$1-v5"; fi
exec bash scripts/ops/school_candidate_bootstrap.sh
