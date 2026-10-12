#!/usr/bin/env bash
set -Eeuo pipefail
umask 077
[[ $# == 4 && "$1" =~ ^(baseline|after)$ ]] || {
    echo 'Usage: school_teacher_probe.sh baseline|after CONTAINER PRIVATE_SELECTION_FILE PUBLIC_REPORT_FILE' >&2
    exit 1
}
MODE=$1
CONTAINER=$2
SELECTION=$(readlink -m "$3")
REPORT=$(readlink -m "$4")
[[ "$SELECTION" == /home/ktdata/school-promotion-private/*/teacher-selection.json && "$REPORT" == /home/ktdata/school-promotion-reports/*/teacher-*.json ]] || exit 1
[[ $(stat -c %a "$(dirname "$SELECTION")") == 700 ]] || { echo 'Selection parent must be root-private.' >&2; exit 1; }
TEMP=/tmp/school-teacher-probe-$$.json
cleanup() { docker exec "$CONTAINER" rm -f "$TEMP" >/dev/null 2>&1 || true; }
trap cleanup EXIT
if [[ "$MODE" == baseline ]]; then
    [[ ! -e "$SELECTION" ]] || { echo 'Existing baseline selection: refusing overwrite.' >&2; exit 1; }
else
    [[ -f "$SELECTION" ]] || exit 1
    docker cp "$SELECTION" "$CONTAINER:$TEMP" >/dev/null
fi
SCRIPT=$(dirname "$(readlink -f "$0")")/school_teacher_probe.py
code=0
timeout 150 docker exec -i "$CONTAINER" python - "$MODE" "$TEMP" <"$SCRIPT" >"$REPORT" 2>"$(dirname "$SELECTION")/teacher-$MODE.stderr.log" || code=$?
if [[ "$MODE" == baseline ]]; then
    docker cp "$CONTAINER:$TEMP" "$SELECTION" >/dev/null
    chmod 600 "$SELECTION"
fi
chmod 644 "$REPORT"
chown ktdata:ktdata "$REPORT"
exit "$code"
