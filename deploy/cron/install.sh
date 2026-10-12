#!/usr/bin/env bash
# Install (or update) the BukuUMKM jobs in the current user's crontab.
# Idempotent: replaces the block between the markers and keeps every other entry.
#
#   deploy/cron/install.sh            install / update
#   deploy/cron/install.sh --remove   remove the block
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BEGIN="# >>> BukuUMKM ($PROJECT_DIR) >>>"
END="# <<< BukuUMKM ($PROJECT_DIR) <<<"

current="$(crontab -l 2>/dev/null || true)"
others="$(printf '%s\n' "$current" | awk -v b="$BEGIN" -v e="$END" '$0 == b {skip = 1; next} $0 == e {skip = 0; next} !skip')"

if [ "${1:-}" = "--remove" ]; then
    printf '%s\n' "$others" | sed '/./,$!d' | crontab -
    echo "Removed the BukuUMKM jobs from $(whoami)'s crontab."
    exit 0
fi

block="$(sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/cron/crontab")"
{
    [ -n "$others" ] && printf '%s\n\n' "$others"
    printf '%s\n%s\n%s\n' "$BEGIN" "$block" "$END"
} | sed '/./,$!d' | crontab -
echo "Installed the BukuUMKM jobs in $(whoami)'s crontab:"
crontab -l | sed -n "\|$BEGIN|,\|$END|p" | grep -v '^#' | grep -v '^$'
