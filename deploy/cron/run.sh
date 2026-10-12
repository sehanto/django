#!/usr/bin/env bash
# Run one manage.py command from cron: right directory, virtualenv and settings, no
# overlapping runs, a log line per run. Silent on success; on failure the output goes
# to stderr so cron mails it (set MAILTO in the crontab).
#
#   deploy/cron/run.sh process_billing
#
# Env overrides: PYTHON (default .venv/bin/python), BUKUUMKM_LOG_DIR (default logs/).
set -uo pipefail

if [ $# -lt 1 ]; then
    echo "usage: $0 <manage.py command> [args...]" >&2
    exit 2
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${PYTHON:-$PROJECT_DIR/.venv/bin/python}"
LOG_DIR="${BUKUUMKM_LOG_DIR:-$PROJECT_DIR/logs}"
COMMAND="$1"
LOG="$LOG_DIR/$COMMAND.log"

cd "$PROJECT_DIR" || exit 1
mkdir -p "$LOG_DIR"

# manage.py defaults to the dev settings and reads .env only after choosing them,
# so take DJANGO_SETTINGS_MODULE from .env when cron's environment doesn't set it.
if [ -z "${DJANGO_SETTINGS_MODULE:-}" ] && [ -f .env ]; then
    DJANGO_SETTINGS_MODULE="$(sed -n 's/^DJANGO_SETTINGS_MODULE=//p' .env | tail -n 1 | tr -d "\"' \r")"
    [ -n "$DJANGO_SETTINGS_MODULE" ] && export DJANGO_SETTINGS_MODULE
fi

exec 9>"$LOG_DIR/$COMMAND.lock"
if ! flock -n 9; then
    echo "$(date -Iseconds) $COMMAND: previous run still in progress; skipped" | tee -a "$LOG" >&2
    exit 75
fi

output="$("$PYTHON" manage.py "$@" 2>&1)"
status=$?
{
    echo "$(date -Iseconds) $* (exit $status)"
    [ -n "$output" ] && echo "$output" | sed 's/^/    /'
} >> "$LOG"

if [ $status -ne 0 ]; then
    echo "BukuUMKM: 'manage.py $*' failed with exit status $status" >&2
    echo "$output" >&2
fi
exit $status
