#!/usr/bin/env bash
# Daily run. Cron example (weekdays 6:30am, server in ET):
#   30 6 * * 1-5  $HOME/auto-apply/scripts/daily.sh >> $HOME/auto-apply/data/cron.log 2>&1
set -euo pipefail
cd "$(dirname "$0")/.."
# The agent CLIs usually install to ~/.local/bin, which cron and `ssh host cmd` leave off PATH.
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
[ -f .venv/bin/activate ] && source .venv/bin/activate  # Docker has it on PATH already
[ -f .env ] && set -a && source .env && set +a
mkdir -p data/daily
# A fresh install does nothing until the dashboard's Setup page has contact details and job interests.
if ! check=$(autoapply setup-check 2>&1); then
  echo "$(date -Is) $check"
  autoapply log-run daily fail "$check" 2>/dev/null || true
  exit 0
fi

# The run is split into short agent sessions so no session drags a long history along (that is where most
# tokens go). Plain steps run first without an agent: email sync and the job search.
autoapply sync-email --days 3 >/dev/null 2>&1 || autoapply log-run sync-email fail "sync failed; see data/cron.log" || true
autoapply find >/dev/null 2>&1 || true

today=data/daily/$(date +%F).md
echo "# Daily run $(date '+%F %H:%M')" > "$today"
ok=1; picked=""; blocked=""

# run_stage STAGE [JOB_ID]: sets line to the stage's last line of output, and ok=0 on failure.
# Agent, model and billing come from Settings > Daily run (light model for rank/prep/drafts).
run_stage() {
  local stage=$1 job=${2:-} agent model via turns last=data/daily/.last-message
  line=""
  read -r agent model via < <(autoapply daily-agent --stage "$stage" 2>/dev/null || echo claude) || true
  agent=${agent:-claude}; via=${via:-subscription}; [ "$model" = - ] && model=
  if [ "$via" = blocked ]; then
    blocked=$(autoapply daily-agent --stage "$stage" 2>&1 >/dev/null | tail -1)
    return 1
  fi
  case $stage in rank) turns=40;; alerts) turns=30;; apply) turns=60;; prep) turns=40;; *) turns=20;; esac
  [ "$via" = openrouter ] && eval "$(autoapply daily-env)"
  rm -f "$last"
  if [ "$agent" = codex ]; then
    local or_args=()
    if [ "$via" = openrouter ]; then
      or_args=(-c model_provider=openrouter
               -c 'model_providers.openrouter={name="OpenRouter",base_url="https://openrouter.ai/api/v1",wire_api="responses",env_key="OPENROUTER_API_KEY"}')
    fi
    # Writes stay inside this folder; network is on so the CLI can reach job boards and ATS pages.
    autoapply daily-prompt "$stage" $job | codex exec --skip-git-repo-check --sandbox workspace-write \
      -c sandbox_workspace_write.network_access=true -c approval_policy=never "${or_args[@]}" \
      ${model:+-m "$model"} -o "$last" - || ok=0
    line=$( [ -s "$last" ] && autoapply log-daily codex "$model" "$via" --stage "$stage" < "$last" || echo "")
  else
    line=$(claude -p "$(autoapply daily-prompt "$stage" $job)" ${model:+--model "$model"} \
      --permission-mode acceptEdits --max-turns "$turns" \
      --output-format json | autoapply log-daily claude "$model" "$via" --stage "$stage") || ok=0
  fi
  echo "- $stage${job:+ $job}: $line" >> "$today"
}

for stage in $(autoapply daily-stages 2>/dev/null); do
  if [ "$stage" = rank ]; then
    # Quick pick (Settings > Daily run): a cheap OpenRouter model reads the alert emails, a short light agent
    # step looks up only the roles that fit, then one cheap call picks. Exit 2 = off today, 1 = failed:
    # either way the rank agent does the whole step instead.
    rc=0; n=$(autoapply quick-pick --alerts 2>>data/cron.log) || rc=$?
    if [ "$rc" = 0 ]; then
      if [ "${n:-0}" -gt 0 ] 2>/dev/null; then run_stage alerts || true; fi
      line=$(autoapply quick-pick 2>>data/cron.log) || rc=$?
    fi
    if [ "$rc" = 0 ]; then picked="$line"; echo "- rank (quick pick): $line" >> "$today"; continue; fi
    [ "$rc" = 1 ] && echo "- rank (quick pick failed, using the agent)" >> "$today"
  fi
  run_stage "$stage" || true
  if [ "$stage" = rank ]; then picked="$line"; fi
done
if [ -z "$blocked" ]; then
  for job in $(autoapply daily-picks 2>/dev/null); do
    run_stage apply "$job" || true
  done
fi
summary=${picked:-No picks today}
if [ -n "$blocked" ]; then
  autoapply log-run daily fail "skipped: $blocked (Settings > Daily run)"
  summary="Daily run skipped: $blocked"
elif [ "$ok" = 1 ]; then
  autoapply log-run daily ok "$summary"
else
  autoapply log-run daily fail "a stage exited with an error; see data/cron.log and $today"
fi

echo "$(date -Is) $summary"
# Morning brief email (today's picks + application status) for the inbox your morning brief reads.
autoapply brief --send || autoapply log-run brief fail "brief email failed; see data/cron.log"
# Optional phone push via ntfy (set NTFY_TOPIC in .env and subscribe in the ntfy app).
if [ -n "${NTFY_TOPIC:-}" ]; then
  todo=$(autoapply todo-line 2>/dev/null || true)
  msg=$summary; [ -n "$todo" ] && msg="$summary
$todo"
  curl -fsS -H "Title: autoapply" -H "Click: ${REVIEW_URL:-}" -d "$msg" "https://ntfy.sh/$NTFY_TOPIC" >/dev/null || true
fi
