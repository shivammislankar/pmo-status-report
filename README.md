# PMO Portfolio Status Report

Turns a project tracker CSV into an email-ready HTML status report with
auto-calculated RAG status and week-over-week trend arrows. No installs needed (Python 3 stdlib).

## Weekly use
1. Update `data/projects.csv` (or export your tracker to that format).
2. Run `python3 status_report.py`
3. Open `reports/status_YYYY-MM-DD.html` → copy/paste into Outlook/Gmail, or attach.

## CSV columns
project, owner, phase, start_date, due_date (YYYY-MM-DD), pct_complete (0-100),
budget, spent, next_milestone, milestone_date, key_risk, status_override (Red/Amber/Green, optional), notes

## RAG rules (tune thresholds at top of status_report.py)
- **Red**: past due, over budget, >20 pts behind time-elapsed plan, or budget burn >20 pts ahead of progress
- **Amber**: >10 pts behind plan / burn ahead, or next milestone date passed
- **Override**: `status_override` wins, but the report notes the calculated status for transparency

Each run saves a snapshot in `history/` so the next run shows ▲ improved / ▼ worsened / ▬ unchanged.

## Pull from Jira (no CSV export needed)
Each portfolio project = one **Epic**. % complete = done child issues ÷ all child issues
(or story points); next milestone = earliest unfinished child issue with a due date.

1. `cp jira_config.example.json jira_config.json` and set `base_url`, `email`, and `jql`
   - Jira Server/Data Center: set `"deployment": "server"`, `"child_link": "epic_link"`, and `epic_link_field`
2. Create an API token: https://id.atlassian.com/manage-profile/security/api-tokens
   (Server/DC: Profile → Personal Access Tokens), then `export JIRA_API_TOKEN=...`
3. `python3 jira_pull.py --test` — confirms connection and shows which epics your JQL matches
4. Optional: find custom field IDs for budget, start date, RAG, risk:
   `python3 jira_pull.py --list-fields budget` → put the `customfield_XXXXX` IDs in `fields`
5. Weekly: `./run_weekly.sh` (pulls from Jira, then builds the report)

Unmapped fields are simply left blank; start date falls back to the epic's created date.

## Automatic Monday email
1. `cp email_config.example.json email_config.json` and set SMTP server, `from`, `to`, `cc`
   - Microsoft 365: `smtp.office365.com:587 starttls` (IT may need to enable SMTP AUTH for your mailbox)
   - Gmail: `smtp.gmail.com:587 starttls` with an **app password** (Google Account → Security → App passwords)
2. Store secrets (scheduled runs can't see your shell's `export`s):
   ```
   mkdir -p ~/.config/pmo-status-report
   cat > ~/.config/pmo-status-report/secrets.env <<X
   JIRA_API_TOKEN=...
   SMTP_PASSWORD=...
   X
   chmod 600 ~/.config/pmo-status-report/secrets.env
   ```
3. Preview: `./run_weekly.sh --dry-run` → open `reports/status_<date>.eml` to see exactly what leadership gets
4. First real send — **send to yourself first** (put only your address in `to`), then switch to the real list
5. Schedule: `./install_schedule.sh` (Mondays 08:00; custom: `./install_schedule.sh "Mon 07:30"`)
   - Laptop off/asleep at 08:00? It runs as soon as you next log in.
   - Logs: `journalctl --user -u pmo-report.service` · Remove: `./install_schedule.sh --remove`

If any step fails (Jira down, token expired…), you get a ⚠ FAILED email instead of leadership getting nothing.

## AI executive summary (optional)
Adds a box at the top: one-line headline, 2–3 sentence summary, **decisions needed this week**, and wins —
written by Claude (`claude-opus-5-5`) from the same data the report shows.

1. **Check your company's AI/data policy first** — project names, owners, budgets, and notes are sent to the Anthropic API.
2. Get an API key at https://platform.claude.com → API Keys
3. Add to `~/.config/pmo-status-report/secrets.env`:
   ```
   ANTHROPIC_API_KEY=sk-ant-...
   PMO_AI_SUMMARY=1
   ```
4. Try it: `set -a; source ~/.config/pmo-status-report/secrets.env; set +a; .venv/bin/python status_report.py --ai-summary`

- Cost: roughly a few cents per weekly report.
- Never blocks the report: no key / API down / declined request → report goes out without the box.
- Cached per date in `reports/summary_<date>.json`; re-running on unchanged data doesn't re-bill.
- The SDK lives in `.venv/` (recreate with `python3 -m venv .venv && .venv/bin/pip install anthropic`).
