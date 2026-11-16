"""
AI executive summary for the portfolio status report, written by Claude.

Called from status_report.py --ai-summary. Needs the `anthropic` package
(installed in .venv) and ANTHROPIC_API_KEY. Any failure returns None so the
report still goes out — just without the summary box.

Results are cached per report date + data hash in reports/summary_<date>.json,
so re-running the report on unchanged data doesn't call the API again.
"""
import hashlib
import json
from pathlib import Path

MODEL = "claude-opus-5-5"

SYSTEM = """You are a senior PMO analyst writing the executive summary at the top of a weekly \
portfolio status report. Your readers are busy executives who will read only this box.

Rules:
- Use only the facts in the data provided. Never invent numbers, dates, causes, or names.
- Lead with what changed since last week and what is most at risk; don't restate every project.
- Plain business language. No jargon like "pts" — say "15 points behind plan" or "about two weeks late".
- Be specific: name the project and owner when it matters.
- decisions_needed: only concrete asks leadership can act on this week (approve, unblock, \
reprioritize, fund, escalate), each tied to a project. Empty list if nothing genuinely needs them.
- wins: completed projects or projects that improved status. Empty list if none."""

SCHEMA = {
    "type": "object",
    "properties": {
        "headline": {"type": "string",
                     "description": "One sentence, under 20 words: the single most important takeaway."},
        "summary": {"type": "string",
                    "description": "2-3 sentences on overall portfolio health and key changes."},
        "decisions_needed": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"project": {"type": "string"}, "ask": {"type": "string"}},
                "required": ["project", "ask"],
                "additionalProperties": False,
            },
        },
        "wins": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["headline", "summary", "decisions_needed", "wins"],
    "additionalProperties": False,
}


def build_facts(projects, prev, prev_date, today):
    """Compact, model-friendly view of the portfolio (only what the report already shows)."""
    return {
        "report_date": today.isoformat(),
        "previous_report_date": prev_date,
        "totals": {
            "projects": len(projects),
            "budget": sum(p["budget_n"] for p in projects),
            "spent": sum(p["spent_n"] for p in projects),
        },
        "projects": [{
            "name": p["project"],
            "owner": p.get("owner", ""),
            "phase": p.get("phase", ""),
            "status": p["status"],
            "last_week_status": prev.get(p["project"]) if prev_date else None,
            "status_reasons": p["reasons"],
            "pct_complete": round(p["pct"]),
            "planned_pct_by_now": round(p["expected"]),
            "budget_spent_pct": round(p["burn_pct"]),
            "due_date": p.get("due_date", ""),
            "next_milestone": p.get("next_milestone", ""),
            "milestone_date": p.get("milestone_date", ""),
            "key_risk": p.get("key_risk", ""),
            "pm_notes": p.get("notes", ""),
        } for p in projects],
    }


def generate(facts, cache_dir: Path):
    payload = json.dumps(facts, sort_keys=True)
    digest = hashlib.sha256(f"{MODEL}\n{SYSTEM}\n{payload}".encode()).hexdigest()[:16]
    cache = cache_dir / f"summary_{facts['report_date']}.json"
    if cache.exists():
        cached = json.loads(cache.read_text())
        if cached.get("hash") == digest:
            print("AI summary: using cached result (data unchanged)")
            return cached["summary"]

    try:
        import anthropic
    except ImportError:
        print("AI summary skipped: 'anthropic' not installed (run with .venv/bin/python).")
        return None

    try:
        client = anthropic.Anthropic()
        response = client.beta.messages.create(
            model=MODEL,
            max_tokens=16000,
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",  # if a request is declined, the API retries on a fallback model
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": SCHEMA}},
            system=SYSTEM,
            messages=[{"role": "user", "content":
                       "Portfolio data for this week's report (JSON):\n\n" + payload}],
        )
    except anthropic.AuthenticationError:
        print("AI summary skipped: invalid or missing ANTHROPIC_API_KEY.")
        return None
    except anthropic.RateLimitError:
        print("AI summary skipped: rate limited — try again later.")
        return None
    except anthropic.APIStatusError as e:
        print(f"AI summary skipped: API error {e.status_code}: {e.message}")
        return None
    except anthropic.APIConnectionError:
        print("AI summary skipped: couldn't reach the Anthropic API.")
        return None
    except Exception as e:  # missing credentials etc. — never block the report
        print(f"AI summary skipped: {e}")
        return None

    if response.stop_reason == "refusal":
        print("AI summary skipped: request was declined.")
        return None
    if response.stop_reason == "max_tokens":
        print("AI summary skipped: response was cut off.")
        return None
    text = next((b.text for b in response.content if b.type == "text"), None)
    try:
        summary = json.loads(text)
    except (TypeError, json.JSONDecodeError):
        print("AI summary skipped: unexpected response format.")
        return None

    cache_dir.mkdir(exist_ok=True)
    cache.write_text(json.dumps({"hash": digest, "model": response.model, "summary": summary}, indent=1))
    u = response.usage
    print(f"AI summary: generated by {response.model} ({u.input_tokens} in / {u.output_tokens} out tokens)")
    return summary


def render_box(summary, escape):
    """HTML box for the top of the report (inline styles for email clients)."""
    if not summary:
        return ""
    decisions = "".join(
        f"<li><b>{escape(d['project'])}:</b> {escape(d['ask'])}</li>" for d in summary["decisions_needed"])
    wins = "".join(f"<li>{escape(w)}</li>" for w in summary["wins"])
    return f"""<div style="border:1px solid #dadce0;border-left:5px solid #1a73e8;background:#f8fbff;
padding:14px 18px;margin:0 0 22px 0;border-radius:4px">
<div style="font-size:11px;color:#1a73e8;font-weight:bold;letter-spacing:.5px">EXECUTIVE SUMMARY</div>
<div style="font-size:17px;font-weight:bold;margin:6px 0">{escape(summary['headline'])}</div>
<div style="font-size:14px;line-height:1.5">{escape(summary['summary'])}</div>
{f'<div style="font-size:13px;font-weight:bold;margin-top:10px">Decisions needed this week</div><ul style="margin:4px 0;padding-left:20px;font-size:13px">{decisions}</ul>' if decisions else ''}
{f'<div style="font-size:13px;font-weight:bold;margin-top:8px">Wins</div><ul style="margin:4px 0;padding-left:20px;font-size:13px">{wins}</ul>' if wins else ''}
<div style="font-size:10px;color:#9aa0a6;margin-top:8px">AI-drafted from the data below — verify before acting.</div>
</div>"""
