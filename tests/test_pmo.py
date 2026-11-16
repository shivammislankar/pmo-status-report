"""Tests for the PMO status report pipeline. Run: python3 -m unittest -v"""
import csv
import html
import io
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import ai_summary  # noqa: E402
import jira_pull  # noqa: E402
import send_report  # noqa: E402
import status_report as sr  # noqa: E402

TODAY = date(2026, 11, 16)


def project(**overrides):
    """A project that is exactly on plan: 50% of the time elapsed, 50% done, 50% spent."""
    row = {"project": "Test", "owner": "PM", "phase": "Execution",
           "start_date": "2026-10-17", "due_date": "2026-12-16", "pct_complete": "50",
           "budget": "100000", "spent": "50000", "next_milestone": "", "milestone_date": "",
           "key_risk": "", "status_override": "", "notes": ""}
    row.update(overrides)
    return sr.assess(row, TODAY)


def quiet(fn, *a, **kw):
    with redirect_stdout(io.StringIO()):
        return fn(*a, **kw)


class RagRules(unittest.TestCase):
    def test_on_plan_is_green(self):
        p = project()
        self.assertEqual(p["status"], "Green")
        self.assertEqual(p["reasons"], [])
        self.assertAlmostEqual(p["expected"], 50, delta=1)

    def test_complete(self):
        self.assertEqual(project(pct_complete="100", due_date="2026-11-01")["status"], "Complete")

    def test_past_due_is_red(self):
        p = project(due_date="2026-11-10")
        self.assertEqual(p["status"], "Red")
        self.assertTrue(any("Past due" in r for r in p["reasons"]))

    def test_over_budget_is_red(self):
        p = project(spent="120000")
        self.assertEqual(p["status"], "Red")
        self.assertTrue(any("Over budget" in r for r in p["reasons"]))

    def test_schedule_thresholds(self):
        self.assertEqual(project(pct_complete="35")["status"], "Amber")   # ~15 pts behind
        self.assertEqual(project(pct_complete="25")["status"], "Red")     # ~25 pts behind
        self.assertEqual(project(pct_complete="45")["status"], "Green")   # ~5 pts behind

    def test_burn_ahead_of_progress(self):
        self.assertEqual(project(pct_complete="55", spent="70000")["status"], "Amber")
        self.assertEqual(project(pct_complete="55", spent="80000")["status"], "Red")

    def test_overdue_milestone_is_amber(self):
        p = project(next_milestone="UAT", milestone_date="2026-11-10")
        self.assertEqual(p["status"], "Amber")

    def test_milestone_soon_flag(self):
        self.assertTrue(project(milestone_date="2026-11-20")["ms_soon"])
        self.assertFalse(project(milestone_date="2026-12-20")["ms_soon"])

    def test_override_wins_but_is_disclosed(self):
        p = project(status_override="red")
        self.assertEqual(p["status"], "Red")
        self.assertIn("Manual override (calc: Green)", p["reasons"])

    def test_non_rag_override_ignored(self):
        self.assertEqual(project(status_override="In Progress")["status"], "Green")

    def test_worst_signal_wins(self):
        p = project(pct_complete="35", spent="120000")  # amber schedule + red budget
        self.assertEqual(p["status"], "Red")
        self.assertGreaterEqual(len(p["reasons"]), 2)


class Parsing(unittest.TestCase):
    def tearDown(self):
        sr.DAYFIRST = False

    def test_date_formats(self):
        for s in ["2026-11-16", "2026/11/16", "11/16/2026", "16-Nov-2026", "Nov 16, 2026",
                  "16 Nov 2026", "16 November 2026",
                  "2026-11-16T10:00:00.000+0000", "11/16/2026 09:30"]:
            self.assertEqual(sr.parse_date(s), TODAY, s)
        self.assertIsNone(sr.parse_date(""))
        with self.assertRaises(ValueError):
            sr.parse_date("soon")

    def test_dayfirst(self):
        sr.DAYFIRST = True
        self.assertEqual(sr.parse_date("03/04/2026"), date(2026, 4, 3))
        sr.DAYFIRST = False
        self.assertEqual(sr.parse_date("03/04/2026"), date(2026, 3, 4))

    def test_numbers(self):
        self.assertEqual(sr.num("$1,250,000"), 1250000)
        self.assertEqual(sr.num("35%"), 35)
        self.assertEqual(sr.num("n/a"), 0)

    def test_header_aliases(self):
        m = sr.normalize_headers(["Project Name", "Project Manager", "Finish Date", "% Complete",
                                  "Actual Cost", "RAG Status", "Department"])
        self.assertEqual(m, {"Project Name": "project", "Project Manager": "owner",
                             "Finish Date": "due_date", "% Complete": "pct_complete",
                             "Actual Cost": "spent", "RAG Status": "status_override"})


class LoadAndRender(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def write_csv(self, text):
        p = self.dir / "in.csv"
        p.write_text(text)
        return p

    def test_messy_export_and_bad_row_skipped(self):
        p = self.write_csv(
            "Project Name,Finish Date,% Complete,Approved Budget,Actual Cost\n"
            'Billing,2027-02-28,40%,"$150,000","$60,000"\n'
            "Broken,not a date,5,10,0\n")
        projects = quiet(sr.load_projects, p, TODAY)
        self.assertEqual([x["project"] for x in projects], ["Billing"])
        self.assertEqual(projects[0]["budget_n"], 150000)

    def test_missing_required_column(self):
        p = self.write_csv("Name,Owner\nX,Y\n")
        with self.assertRaises(SystemExit):
            quiet(sr.load_projects, p, TODAY)

    def test_html_escapes_user_text(self):
        p = self.write_csv("project,due_date,pct_complete,notes\n"
                           '<script>alert(1)</script>,2027-01-01,10,"<b>x</b>"\n')
        out, _ = quiet(sr.build_report, p, TODAY, self.dir / "out", self.dir / "hist")
        page = out.read_text()
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)

    def test_trend_across_weeks(self):
        p = self.write_csv("project,start_date,due_date,pct_complete\n"
                           "Slipping,2026-10-01,2026-12-31,36\n")
        hist = self.dir / "hist"
        quiet(sr.build_report, p, date(2026, 10, 20), self.dir / "out", hist)
        out, projects = quiet(sr.build_report, p, TODAY, self.dir / "out", hist)
        self.assertEqual(json.loads((hist / "2026-10-20.json").read_text())["Slipping"], "Green")
        self.assertEqual(projects[0]["status"], "Amber")
        self.assertIn("&#9660;", out.read_text())  # down arrow: got worse

    def test_sample_data_is_stable_for_demo_dates(self):
        counts = []
        for week in sr.DEMO_WEEKS:
            _, ps = quiet(sr.build_report, ROOT / "data" / "projects.csv", week,
                          self.dir / "demo", self.dir / "demo_hist")
            counts.append({s: sum(x["status"] == s for x in ps) for s in sr.RANK})
        self.assertEqual(counts[-1], {"Red": 2, "Amber": 4, "Green": 1, "Complete": 1})


class Jira(unittest.TestCase):
    def test_text_flattening(self):
        adf = {"type": "doc", "content": [{"type": "paragraph", "content": [
            {"type": "text", "text": "Vendor "}, {"type": "text", "text": "delay"}]}]}
        self.assertEqual(jira_pull.text(adf), "Vendor delay")
        self.assertEqual(jira_pull.text({"displayName": "Priya S"}), "Priya S")
        self.assertEqual(jira_pull.text({"value": "Amber"}), "Amber")
        self.assertEqual(jira_pull.text([{"name": "a"}, {"name": "b"}]), "a, b")
        self.assertEqual(jira_pull.text(None), "")
        self.assertEqual(jira_pull.text(250000), "250000")

    def test_chunks(self):
        self.assertEqual(list(jira_pull.chunks(list(range(5)), 2)), [[0, 1], [2, 3], [4]])


class Email(unittest.TestCase):
    CFG = {"from": "PMO <pmo@example.com>", "to": ["a@example.com", "b@example.com"],
           "cc": ["c@example.com"]}

    def test_report_email_structure(self):
        with tempfile.TemporaryDirectory() as d:
            report = Path(d) / "status_2026-11-16.html"
            report.write_text("<html><body><h1>Hi</h1><li>Red project</li></body></html>")
            msg = send_report.build(self.CFG, "Subject", html=report.read_text(), attachment=report)
        self.assertEqual(msg["To"], "a@example.com, b@example.com")
        self.assertEqual(msg["Cc"], "c@example.com")
        types = [p.get_content_type() for p in msg.walk()]
        self.assertIn("text/plain", types)
        self.assertIn("text/html", types)
        self.assertIn("status_2026-11-16.html", [p.get_filename() for p in msg.walk()])

    def test_html_to_text(self):
        self.assertEqual(send_report.html_to_text("<h1>Title</h1><p>A &amp; B</p>"), "Title\nA & B")


class AiSummary(unittest.TestCase):
    SUMMARY = {"headline": "<b>Bad</b> week", "summary": "Two Red.",
               "decisions_needed": [{"project": "X", "ask": "Approve funds"}], "wins": []}

    def test_box_escapes_model_output(self):
        box = ai_summary.render_box(self.SUMMARY, html.escape)
        self.assertIn("&lt;b&gt;Bad&lt;/b&gt;", box)
        self.assertIn("Decisions needed", box)
        self.assertNotIn(">Wins<", box)
        self.assertEqual(ai_summary.render_box(None, html.escape), "")

    def test_cache_hit_skips_api(self):
        facts = {"report_date": "2026-11-16", "projects": []}
        with tempfile.TemporaryDirectory() as d:
            payload = json.dumps(facts, sort_keys=True)
            import hashlib
            digest = hashlib.sha256(
                f"{ai_summary.MODEL}\n{ai_summary.SYSTEM}\n{payload}".encode()).hexdigest()[:16]
            Path(d, "summary_2026-11-16.json").write_text(
                json.dumps({"hash": digest, "summary": self.SUMMARY}))
            self.assertEqual(quiet(ai_summary.generate, facts, Path(d)), self.SUMMARY)

    def test_schema_requires_all_fields(self):
        self.assertEqual(set(ai_summary.SCHEMA["required"]), set(ai_summary.SCHEMA["properties"]))
        self.assertFalse(ai_summary.SCHEMA["additionalProperties"])


if __name__ == "__main__":
    unittest.main()
