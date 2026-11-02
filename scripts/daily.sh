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

# The agent, its model, and where the model comes from are picked on the dashboard's Settings page:
# Claude Code or the Codex CLI, on its subscription (run `claude` or `codex login` once) or on an
# OpenRouter key (paid per token, stops at the monthly budget).
read -r agent model via < <(autoapply daily-agent 2>/dev/null || echo claude) || true
agent=${agent:-claude}; via=${via:-subscription}
[ "$model" = - ] && model=
last=data/daily/.last-message
rm -f "$last"
if [ "$via" = blocked ]; then
  why=$(autoapply daily-agent 2>&1 >/dev/null | tail -1)
  echo "$(date -Is) daily run skipped: ${why:-OpenRouter not usable}"
  autoapply log-run daily fail "skipped: ${why:-OpenRouter not usable} (Settings > Daily run)"
  ok=skip
else
  if [ "$via" = openrouter ]; then
    eval "$(autoapply daily-env)"
  fi
  if [ "$agent" = codex ]; then
    or_args=()
    if [ "$via" = openrouter ]; then
      # OpenRouter as a custom Codex provider (Responses API), key from the environment.
      or_args=(-c model_provider=openrouter
               -c 'model_providers.openrouter={name="OpenRouter",base_url="https://openrouter.ai/api/v1",wire_api="responses",env_key="OPENROUTER_API_KEY"}')
    fi
    # Writes stay inside this folder; network is on so the CLI can reach job boards and ATS pages.
    codex exec --skip-git-repo-check --sandbox workspace-write \
      -c sandbox_workspace_write.network_access=true -c approval_policy=never "${or_args[@]}" \
      ${model:+-m "$model"} -o "$last" - < prompts/daily.md && ok=1 || ok=0
    summary=$( [ -s "$last" ] && autoapply log-daily codex "$model" "$via" < "$last" || echo "")
  else
    # JSON output carries token counts and the API-price estimate, logged for the Settings page.
    summary=$(claude -p "$(cat prompts/daily.md)" ${model:+--model "$model"} \
      --permission-mode acceptEdits \
      --max-turns 120 \
      --output-format json | autoapply log-daily claude "$model" "$via") && ok=1 || ok=0
  fi
fi
if [ "$ok" = 1 ]; then
  autoapply log-run daily ok "${summary:-done ($agent)}"
elif [ "$ok" = skip ]; then
  summary="Daily run skipped; see Settings > Daily run."
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
