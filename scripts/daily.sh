#!/usr/bin/env bash
# Daily run. Cron example (weekdays 6:30am, server in ET):
#   30 6 * * 1-5  $HOME/auto-apply/scripts/daily.sh >> $HOME/auto-apply/data/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
source .venv/bin/activate
[ -f .env ] && set -a && source .env && set +a
mkdir -p data/daily

# Headless Claude Code run using your Claude subscription login (run `claude` once to sign in).
if summary=$(claude -p "$(cat prompts/daily.md)" \
  --permission-mode acceptEdits \
  --max-turns 120 \
  --output-format text | tail -n 1); then
  autoapply log-run daily ok "$summary"
else
  autoapply log-run daily fail "claude run exited with an error; see data/cron.log"
fi

echo "$(date -Is) $summary"
# Morning brief email (today's picks + application status) for the inbox your morning brief reads.
autoapply brief --send || autoapply log-run brief fail "brief email failed; see data/cron.log"
# Optional phone push via ntfy (set NTFY_TOPIC in .env and subscribe in the ntfy app).
if [ -n "${NTFY_TOPIC:-}" ]; then
  curl -fsS -H "Title: autoapply" -H "Click: ${REVIEW_URL:-}" -d "$summary" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
fi
