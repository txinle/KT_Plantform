#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $# == 3 ]] || { echo 'Usage: school_student_probe.sh CONTAINER PRIVATE_SELECTION_FILE PUBLIC_REPORT_FILE' >&2; exit 1; }
CONTAINER=$1
SELECTION=$(readlink -m "$2")
REPORT=$(readlink -m "$3")
[[ "$SELECTION" == /home/ktdata/school-promotion-private/*/student-selection.json && "$REPORT" == /home/ktdata/school-promotion-reports/*/student-*.json ]] || exit 1
[[ $(stat -c %a "$(dirname "$SELECTION")") == 700 ]] || { echo 'Selection parent must be root-private.' >&2; exit 1; }
[[ ! -e "$REPORT" ]] || { echo 'Refusing to overwrite probe evidence.' >&2; exit 1; }
TEMP=/tmp/school-student-probe-$$.json
cleanup() { docker exec "$CONTAINER" rm -f "$TEMP" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if [[ -f "$SELECTION" ]]; then
    docker cp "$SELECTION" "$CONTAINER:$TEMP" >/dev/null
fi
SCRIPT=$(dirname "$(readlink -f "$0")")/school_student_probe.py
code=0
timeout 240 docker exec -i "$CONTAINER" python - "$TEMP" <"$SCRIPT" >"$REPORT" 2>"$(dirname "$SELECTION")/student-probe.stderr.log" || code=$?
if [[ ! -e "$SELECTION" ]] && docker exec "$CONTAINER" test -f "$TEMP"; then
    docker cp "$CONTAINER:$TEMP" "$SELECTION" >/dev/null
    chmod 600 "$SELECTION"
fi
chmod 644 "$REPORT"
chown ktdata:ktdata "$REPORT"
exit "$code"
