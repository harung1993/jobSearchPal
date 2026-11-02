#!/usr/bin/env bash
# Daily run. Cron example (weekdays 6:30am, server in ET):
#   30 6 * * 1-5  $HOME/auto-apply/scripts/daily.sh >> $HOME/auto-apply/data/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .venv/bin/activate ] && source .venv/bin/activate  # Docker has it on PATH already
[ -f .env ] && set -a && source .env && set +a
mkdir -p data/daily
# A fresh install does nothing until the dashboard's Setup page has contact details and job interests.
if ! check=$(autoapply setup-check 2>&1); then
  echo "$(date -Is) $check"
  autoapply log-run daily fail "$check" 2>/dev/null || true
  exit 0
fi

# The agent and model are picked on the dashboard's Settings page: Claude Code on a Claude subscription
# (run `claude` once to sign in) or the Codex CLI on a ChatGPT subscription (run `codex login` once).
read -r agent model < <(autoapply daily-agent 2>/dev/null || echo claude) || true
agent=${agent:-claude}
last=data/daily/.last-message
rm -f "$last"
if [ "$agent" = codex ]; then
  # Writes stay inside this folder; network is on so the CLI can reach job boards and ATS pages.
  codex exec --skip-git-repo-check --sandbox workspace-write \
    -c sandbox_workspace_write.network_access=true -c approval_policy=never \
    ${model:+-m "$model"} -o "$last" - < prompts/daily.md && ok=1 || ok=0
  summary=$( [ -s "$last" ] && autoapply log-daily codex "$model" < "$last" || echo "")
else
  # JSON output carries token counts and the API-price estimate, logged for the Settings page.
  summary=$(claude -p "$(cat prompts/daily.md)" ${model:+--model "$model"} \
    --permission-mode acceptEdits \
    --max-turns 120 \
    --output-format json | autoapply log-daily claude "$model") && ok=1 || ok=0
fi
if [ "$ok" = 1 ]; then
  autoapply log-run daily ok "${summary:-done ($agent)}"
else
  autoapply log-run daily fail "$agent run exited with an error; see data/cron.log"
fi

echo "$(date -Is) $summary"
# Morning brief email (today's picks + application status) for the inbox your morning brief reads.
autoapply brief --send || autoapply log-run brief fail "brief email failed; see data/cron.log"
# Optional phone push via ntfy (set NTFY_TOPIC in .env and subscribe in the ntfy app).
if [ -n "${NTFY_TOPIC:-}" ]; then
  curl -fsS -H "Title: autoapply" -H "Click: ${REVIEW_URL:-}" -d "$summary" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
fi
