#!/usr/bin/env bash
# Weekly pipeline: [Jira pull] -> build report -> email it.
# Emails a failure alert to you if any step breaks.
#   ./run_weekly.sh             full run
#   ./run_weekly.sh --dry-run   build everything, save email as .eml, don't send
set -uo pipefail
cd "$(dirname "$0")"

SECRETS="${PMO_SECRETS:-$HOME/.config/pmo-status-report/secrets.env}"
[[ -f "$SECRETS" ]] && set -a && source "$SECRETS" && set +a

DRY=""; [[ "${1:-}" == "--dry-run" ]] && DRY="--dry-run"
mkdir -p reports
LOG="reports/last_run.log"

run() {
  echo "=== $(date '+%F %T') weekly run ${DRY}"
  if [[ -f jira_config.json ]]; then
    python3 jira_pull.py || return 1
    INPUT=data/jira_projects.csv
  else
    echo "No jira_config.json — using data/projects.csv"
    INPUT=data/projects.csv
  fi
  AI=""; [[ "${PMO_AI_SUMMARY:-0}" == "1" ]] && AI="--ai-summary"
  PY=python3; [[ -x .venv/bin/python ]] && PY=.venv/bin/python
  "$PY" status_report.py -i "$INPUT" $AI ${PMO_REPORT_ARGS:-} || return 1
  python3 send_report.py $DRY || return 1
}

if run 2>&1 | tee "$LOG"; [[ ${PIPESTATUS[0]} -eq 0 ]]; then
  echo "Done."
else
  echo "Weekly run FAILED — sending alert." | tee -a "$LOG"
  python3 send_report.py --failure "$LOG" $DRY
  exit 1
fi
