#!/usr/bin/env bash
# Install (or update) the EA-System jobs in the current user's crontab.
# Idempotent: replaces the block between the markers and keeps every other entry.
#
#   deploy/cron/install.sh            install / update
#   deploy/cron/install.sh --remove   remove the block
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BEGIN="# >>> EA-System ($PROJECT_DIR) >>>"
END="# <<< EA-System ($PROJECT_DIR) <<<"
# Blocks installed before the rename from BukuUMKM are replaced too.
LEGACY_BEGIN="# >>> BukuUMKM ($PROJECT_DIR) >>>"
LEGACY_END="# <<< BukuUMKM ($PROJECT_DIR) <<<"

current="$(crontab -l 2>/dev/null || true)"
others="$(printf '%s\n' "$current" | awk -v b="$BEGIN" -v e="$END" -v lb="$LEGACY_BEGIN" -v le="$LEGACY_END" \
    '$0 == b || $0 == lb {skip = 1; next} $0 == e || $0 == le {skip = 0; next} !skip')"

if [ "${1:-}" = "--remove" ]; then
    printf '%s\n' "$others" | sed '/./,$!d' | crontab -
    echo "Removed the EA-System jobs from $(whoami)'s crontab."
    exit 0
fi

block="$(sed "s|@PROJECT_DIR@|$PROJECT_DIR|g" "$PROJECT_DIR/deploy/cron/crontab")"
{
    [ -n "$others" ] && printf '%s\n\n' "$others"
    printf '%s\n%s\n%s\n' "$BEGIN" "$block" "$END"
} | sed '/./,$!d' | crontab -
echo "Installed the EA-System jobs in $(whoami)'s crontab:"
crontab -l | sed -n "\|$BEGIN|,\|$END|p" | grep -v '^#' | grep -v '^$'
