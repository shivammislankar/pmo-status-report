#!/usr/bin/env python3
"""
PMO Portfolio Status Report generator (stdlib only).

Reads a project CSV, auto-calculates RAG status from schedule and budget
signals, compares with last week's snapshot to show trend, and writes an
email-ready HTML report.

Usage:
    python3 status_report.py                      # uses data/projects.csv, today
    python3 status_report.py -i my.csv --as-of 2026-11-16
"""
import argparse
import csv
import html
import json
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).parent

# ---- RAG thresholds (percentage points) — tune to your PMO's standards ----
SCHEDULE_AMBER = 10   # actual % complete behind time-elapsed % by this much
SCHEDULE_RED = 20
BURN_AMBER = 10       # budget % spent ahead of % complete by this much
BURN_RED = 20
MILESTONE_SOON_DAYS = 7

RANK = {"Red": 0, "Amber": 1, "Green": 2, "Complete": 3}
COLORS = {"Red": "#d93025", "Amber": "#f29900", "Green": "#188038", "Complete": "#5f6368"}


# Real tracker exports use different headers — map common variants to ours.
ALIASES = {
    "project": ["project", "project name", "name", "title", "initiative", "summary"],
    "owner": ["owner", "project manager", "pm", "lead", "assignee", "responsible"],
    "phase": ["phase", "stage", "status phase", "lifecycle"],
    "start_date": ["start_date", "start", "start date", "planned start", "baseline start"],
    "due_date": ["due_date", "due", "due date", "end", "end date", "finish", "finish date",
                 "target date", "planned finish", "go live", "go-live date"],
    "pct_complete": ["pct_complete", "% complete", "percent complete", "progress", "complete %", "% done"],
    "budget": ["budget", "approved budget", "total budget", "budget amount", "baseline cost"],
    "spent": ["spent", "actual", "actuals", "actual cost", "cost to date", "spend", "spent to date"],
    "next_milestone": ["next_milestone", "next milestone", "milestone"],
    "milestone_date": ["milestone_date", "milestone date", "next milestone date"],
    "key_risk": ["key_risk", "key risk", "risk", "top risk", "risks", "issues"],
    "status_override": ["status_override", "status", "rag", "rag status", "health", "overall status"],
    "notes": ["notes", "comments", "update", "status update", "commentary", "remarks"],
}
REQUIRED = ["project", "due_date", "pct_complete"]
DAYFIRST = False  # set via --dayfirst for DD/MM/YYYY exports


def normalize_headers(fieldnames):
    """Map the file's headers to canonical names; return {file_header: canonical}."""
    lookup = {a: canon for canon, alist in ALIASES.items() for a in alist}
    mapping = {}
    for h in fieldnames:
        key = " ".join((h or "").strip().lower().replace("_", " ").split())
        canon = lookup.get(key) or lookup.get(key.replace(" ", "_"))
        if canon and canon not in mapping.values():
            mapping[h] = canon
    return mapping


def parse_date(s):
    if not s or not str(s).strip():
        return None
    s = str(s).strip().split(" ")[0].split("T")[0]  # drop any time part
    slash = ["%d/%m/%Y", "%m/%d/%Y", "%d/%m/%y", "%m/%d/%y"]
    if not DAYFIRST:
        slash = [slash[1], slash[0], slash[3], slash[2]]
    for fmt in ["%Y-%m-%d", "%Y/%m/%d", *slash, "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%d-%m-%Y"]:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    raise ValueError(f"Unrecognised date: {s!r}")


def num(s, default=0.0):
    try:
        return float(str(s).replace(",", "").replace("$", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return default


def assess(row, today):
    """Return the project dict enriched with RAG status and reasons."""
    start, due = parse_date(row["start_date"]), parse_date(row["due_date"])
    ms_date = parse_date(row.get("milestone_date", ""))
    pct = num(row["pct_complete"])
    budget, spent = num(row["budget"]), num(row["spent"])

    expected = 0.0
    if start and due and due > start:
        expected = max(0.0, min(100.0, (today - start).days / (due - start).days * 100))
    sched_gap = expected - pct
    burn_pct = spent / budget * 100 if budget else 0.0
    burn_gap = burn_pct - pct

    reasons, status = [], "Green"

    def flag(level, why):
        nonlocal status
        reasons.append(why)
        if RANK[level] < RANK[status]:
            status = level

    if pct >= 100:
        status = "Complete"
    else:
        if due and today > due:
            flag("Red", f"Past due date ({due:%d %b})")
        if spent > budget > 0:
            flag("Red", f"Over budget ({burn_pct:.0f}% spent)")
        if sched_gap > SCHEDULE_RED:
            flag("Red", f"{sched_gap:.0f} pts behind schedule")
        elif sched_gap > SCHEDULE_AMBER:
            flag("Amber", f"{sched_gap:.0f} pts behind schedule")
        if burn_gap > BURN_RED:
            flag("Red", f"Burn {burn_gap:.0f} pts ahead of progress")
        elif burn_gap > BURN_AMBER:
            flag("Amber", f"Burn {burn_gap:.0f} pts ahead of progress")
        if ms_date and ms_date < today:
            flag("Amber", f"Milestone overdue: {row.get('next_milestone','')}")

    calculated = status
    override = (row.get("status_override") or "").strip().title()
    if override in RANK:
        status = override
        if override != calculated:
            reasons.append(f"Manual override (calc: {calculated})")

    ms_soon = bool(ms_date and 0 <= (ms_date - today).days <= MILESTONE_SOON_DAYS and pct < 100)

    return {
        **row,
        "pct": pct, "expected": expected, "budget_n": budget, "spent_n": spent,
        "burn_pct": burn_pct, "status": status, "reasons": reasons,
        "ms_date": ms_date, "ms_soon": ms_soon,
    }


def load_previous(history_dir, today):
    snaps = sorted(p for p in history_dir.glob("*.json") if p.stem < today.isoformat())
    if not snaps:
        return {}, None
    return json.loads(snaps[-1].read_text()), snaps[-1].stem


def trend(cur, prev):
    if prev is None:
        return ("NEW", "#1a73e8")
    if cur == prev:
        return ("&#9644;", "#5f6368")  # flat
    better = RANK[cur] > RANK[prev]
    return ("&#9650;" if better else "&#9660;", "#188038" if better else "#d93025")


def pill(status):
    return (f'<span style="background:{COLORS[status]};color:#fff;padding:2px 10px;'
            f'border-radius:10px;font-size:12px;font-weight:bold">{status}</span>')


def money(n):
    return f"${n/1000:,.0f}k" if n < 1_000_000 else f"${n/1_000_000:,.2f}M"


def render(projects, prev, prev_date, today, summary=None):
    e = html.escape
    summary_box = ""
    if summary:
        from ai_summary import render_box
        summary_box = render_box(summary, e)
    counts = {s: sum(p["status"] == s for p in projects) for s in RANK}
    tot_budget = sum(p["budget_n"] for p in projects)
    tot_spent = sum(p["spent_n"] for p in projects)

    tiles = "".join(
        f'<td style="padding:12px 18px;border-left:5px solid {COLORS[s]};background:#f8f9fa">'
        f'<div style="font-size:26px;font-weight:bold">{counts[s]}</div>'
        f'<div style="font-size:12px;color:#5f6368">{s}</div></td><td width="10"></td>'
        for s in RANK)

    attention = [p for p in projects if p["status"] in ("Red", "Amber")]
    att_rows = "".join(
        f'<li style="margin-bottom:6px">{pill(p["status"])} <b>{e(p["project"])}</b> '
        f'({e(p["owner"])}): {e("; ".join(p["reasons"]) or "—")}'
        + (f' — <i>Risk: {e(p["key_risk"])}</i>' if p.get("key_risk") else "") + "</li>"
        for p in attention) or "<li>None 🎉</li>"

    upcoming = sorted((p for p in projects if p["ms_soon"]), key=lambda p: p["ms_date"])
    up_rows = "".join(
        f'<li><b>{p["ms_date"]:%a %d %b}</b> — {e(p["project"])}: {e(p["next_milestone"])}</li>'
        for p in upcoming) or "<li>No milestones in the next 7 days</li>"

    th = 'style="text-align:left;padding:8px;border-bottom:2px solid #dadce0;font-size:12px;color:#5f6368"'
    td = 'style="padding:8px;border-bottom:1px solid #eee;font-size:13px;vertical-align:top"'
    rows = ""
    for p in projects:
        arrow, acolor = trend(p["status"], prev.get(p["project"]) if prev_date else p["status"])
        bar_color = COLORS[p["status"]]
        rows += (
            f"<tr><td {td}><b>{e(p['project'])}</b><br><span style='color:#5f6368'>{e(p['phase'])}</span></td>"
            f"<td {td}>{e(p['owner'])}</td>"
            f"<td {td}>{pill(p['status'])} <span style='color:{acolor}'>{arrow}</span></td>"
            f"<td {td}><div style='background:#eee;width:110px;height:8px;border-radius:4px'>"
            f"<div style='background:{bar_color};width:{min(p['pct'],100)*1.1:.0f}px;height:8px;border-radius:4px'></div></div>"
            f"{p['pct']:.0f}% <span style='color:#5f6368'>(plan {p['expected']:.0f}%)</span></td>"
            f"<td {td}>{money(p['spent_n'])} / {money(p['budget_n'])}<br>"
            f"<span style='color:#5f6368'>{p['burn_pct']:.0f}% burned</span></td>"
            f"<td {td}>{e(p['due_date'])}</td>"
            f"<td {td}>{e(p.get('next_milestone',''))}<br><span style='color:#5f6368'>{e(p.get('milestone_date',''))}</span></td>"
            f"<td {td}>{e(p.get('notes',''))}</td></tr>")

    trend_note = (f"Trend vs. snapshot of {prev_date}" if prev_date
                  else "No previous snapshot — trend arrows start next run")

    return f"""<!DOCTYPE html><html><head><meta charset="utf-8">
<title>Portfolio Status — {today:%d %b %Y}</title></head>
<body style="font-family:Segoe UI,Arial,sans-serif;color:#202124;max-width:1100px;margin:24px auto;padding:0 16px">
<h1 style="margin-bottom:4px">Portfolio Status Report</h1>
<div style="color:#5f6368;margin-bottom:20px">Week of {today:%d %B %Y} · {len(projects)} projects ·
Budget {money(tot_spent)} of {money(tot_budget)} spent ({tot_spent/tot_budget*100 if tot_budget else 0:.0f}%)</div>
{summary_box}<table cellspacing="0"><tr>{tiles}</tr></table>
<h2 style="font-size:18px;margin-top:28px">⚠ Needs attention</h2><ul style="padding-left:18px">{att_rows}</ul>
<h2 style="font-size:18px">📅 Milestones in the next 7 days</h2><ul style="padding-left:18px">{up_rows}</ul>
<h2 style="font-size:18px">All projects</h2>
<table cellspacing="0" style="border-collapse:collapse;width:100%">
<tr><th {th}>Project</th><th {th}>Owner</th><th {th}>Status</th><th {th}>Progress</th>
<th {th}>Budget</th><th {th}>Due</th><th {th}>Next milestone</th><th {th}>Notes</th></tr>
{rows}</table>
<p style="color:#9aa0a6;font-size:11px;margin-top:20px">{trend_note}. RAG auto-calculated:
Red = past due, over budget, or &gt;{SCHEDULE_RED} pts behind plan / burn ahead of progress;
Amber = &gt;{SCHEDULE_AMBER} pts or overdue milestone. Manual overrides noted.</p>
</body></html>"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--input", default=ROOT / "data" / "projects.csv", type=Path)
    ap.add_argument("-o", "--outdir", default=ROOT / "reports", type=Path)
    ap.add_argument("--as-of", type=lambda s: parse_date(s), default=date.today())
    ap.add_argument("--dayfirst", action="store_true", help="dates are DD/MM/YYYY")
    ap.add_argument("--ai-summary", action="store_true",
                    help="add a Claude-written executive summary (needs ANTHROPIC_API_KEY)")
    args = ap.parse_args()
    global DAYFIRST
    DAYFIRST = args.dayfirst
    today = args.as_of

    with open(args.input, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        mapping = normalize_headers(reader.fieldnames or [])
        missing = [c for c in REQUIRED if c not in mapping.values()]
        if missing:
            raise SystemExit(f"Missing required column(s): {', '.join(missing)}\n"
                             f"Found headers: {reader.fieldnames}\n"
                             f"Rename them or add aliases in ALIASES at the top of the script.")
        ignored = [h for h in reader.fieldnames if h not in mapping]
        renamed = ", ".join(f"{h!r}->{c}" for h, c in mapping.items() if h != c)
        print(f"Columns mapped: {renamed or 'all standard'}")
        if ignored:
            print(f"Ignored columns: {', '.join(ignored)}")
        projects = []
        for i, raw in enumerate(reader, start=2):
            row = {c: "" for c in ALIASES} | {mapping[h]: (v or "") for h, v in raw.items() if h in mapping}
            if not row["project"].strip():
                continue
            try:
                projects.append(assess(row, today))
            except ValueError as err:
                print(f"  ! Row {i} ({row['project']}): {err} — skipped")
    projects.sort(key=lambda p: (RANK[p["status"]], p["project"]))

    history = ROOT / "history"
    history.mkdir(exist_ok=True)
    prev, prev_date = load_previous(history, today)

    args.outdir.mkdir(exist_ok=True)
    summary = None
    if args.ai_summary:
        from ai_summary import build_facts, generate
        summary = generate(build_facts(projects, prev, prev_date, today), args.outdir)
    out = args.outdir / f"status_{today.isoformat()}.html"
    out.write_text(render(projects, prev, prev_date, today, summary), encoding="utf-8")
    (history / f"{today.isoformat()}.json").write_text(
        json.dumps({p["project"]: p["status"] for p in projects}, indent=1))

    summary = ", ".join(f"{sum(p['status']==s for p in projects)} {s}" for s in RANK)
    print(f"Report: {out}\n{summary}")


if __name__ == "__main__":
    main()
