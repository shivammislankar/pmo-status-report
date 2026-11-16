#!/usr/bin/env python3
"""
Pull portfolio projects (Epics) from Jira into the CSV that status_report.py reads.

Setup:
    cp jira_config.example.json jira_config.json   # then edit it
    export JIRA_API_TOKEN=...                       # never put the token in the file

Usage:
    python3 jira_pull.py --test                 # check connection + show matching epics
    python3 jira_pull.py --list-fields budget   # find custom field IDs by name
    python3 jira_pull.py                        # write data/jira_projects.csv
"""
import argparse
import base64
import csv
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
COLUMNS = ["project", "owner", "phase", "start_date", "due_date", "pct_complete", "budget",
           "spent", "next_milestone", "milestone_date", "key_risk", "status_override", "notes",
           "jira_key"]


class Jira:
    def __init__(self, cfg):
        self.base = cfg["base_url"].rstrip("/")
        self.cloud = cfg.get("deployment", "cloud").lower() == "cloud"
        self.api = "/rest/api/3" if self.cloud else "/rest/api/2"
        token = os.environ.get("JIRA_API_TOKEN")
        if not token:
            sys.exit("Set the JIRA_API_TOKEN environment variable first.")
        if self.cloud:
            if not cfg.get("email"):
                sys.exit("Jira Cloud needs your login 'email' in jira_config.json.")
            raw = f"{cfg['email']}:{token}".encode()
            self.auth = "Basic " + base64.b64encode(raw).decode()
        else:
            self.auth = f"Bearer {token}"  # Server/Data Center personal access token

    def request(self, method, path, body=None):
        req = urllib.request.Request(
            self.base + path, method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": self.auth, "Accept": "application/json",
                     "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            hint = {401: "Check email/token (Cloud) or PAT (Server).",
                    403: "Your account lacks permission for this.",
                    404: "Check base_url and 'deployment' (cloud vs server)."}.get(e.code, "")
            sys.exit(f"Jira API error {e.code} on {path}: {detail}\n{hint}")
        except urllib.error.URLError as e:
            sys.exit(f"Can't reach {self.base}: {e.reason}")

    def search(self, jql, fields):
        """Yield all issues matching JQL, handling both Cloud and Server pagination."""
        if self.cloud:
            token = None
            while True:
                body = {"jql": jql, "fields": fields, "maxResults": 100}
                if token:
                    body["nextPageToken"] = token
                page = self.request("POST", f"{self.api}/search/jql", body)
                yield from page.get("issues", [])
                token = page.get("nextPageToken")
                if not token or page.get("isLast"):
                    return
        else:
            start = 0
            while True:
                page = self.request("POST", f"{self.api}/search",
                                    {"jql": jql, "fields": fields, "startAt": start, "maxResults": 100})
                issues = page.get("issues", [])
                yield from issues
                start += len(issues)
                if not issues or start >= page.get("total", 0):
                    return

    def fields(self):
        return self.request("GET", f"{self.api}/field")


def text(value):
    """Flatten any Jira field value (user, option, ADF rich text, list...) to plain text."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float)):
        return str(value)
    if isinstance(value, list):
        return ", ".join(filter(None, (text(v) for v in value)))
    if isinstance(value, dict):
        if value.get("type") == "doc" or "content" in value:  # Atlassian Document Format
            parts = []

            def walk(n):
                if isinstance(n, dict):
                    if n.get("type") == "text":
                        parts.append(n.get("text", ""))
                    for c in n.get("content", []):
                        walk(c)
                    if n.get("type") in ("paragraph", "listItem"):
                        parts.append(" ")
            walk(value)
            return " ".join("".join(parts).split())
        for k in ("displayName", "value", "name", "key"):
            if k in value:
                return str(value[k])
    return str(value)


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def pull(jira, cfg):
    f = cfg.get("fields", {})
    custom = {k: v for k, v in f.items() if v}
    epic_fields = ["summary", "assignee", "status", "duedate", "created", *custom.values()]
    epics = list(jira.search(cfg["jql"], sorted(set(epic_fields))))
    if not epics:
        return []

    # ---- children of each epic, for progress + next milestone ----
    link = cfg.get("child_link", "parent")              # "parent" (Cloud) or "epic_link" (Server)
    link_field = cfg.get("epic_link_field", "")         # Server only, e.g. customfield_10008
    sp_field = cfg.get("story_points_field", "")
    use_points = cfg.get("progress", "issue_count") == "story_points" and sp_field
    child_fields = ["summary", "status", "duedate", "parent"]
    if link == "epic_link":
        if not link_field:
            sys.exit("child_link=epic_link needs 'epic_link_field' (find it with --list-fields epic).")
        child_fields.append(link_field)
    if use_points:
        child_fields.append(sp_field)

    children = {e["key"]: [] for e in epics}
    clause = "parent in ({})" if link == "parent" else '"Epic Link" in ({})'
    for batch in chunks(list(children), 50):
        for c in jira.search(clause.format(",".join(batch)), child_fields):
            cf = c["fields"]
            parent = (cf.get("parent") or {}).get("key") if link == "parent" else cf.get(link_field)
            if isinstance(parent, dict):
                parent = parent.get("key")
            if parent in children:
                children[parent].append(cf)

    rows = []
    for e in epics:
        ef = e["fields"]
        kids = children[e["key"]]
        is_done = lambda x: (x.get("status") or {}).get("statusCategory", {}).get("key") == "done"
        weight = (lambda x: float(x.get(sp_field) or 0)) if use_points else (lambda x: 1.0)
        total = sum(weight(k) for k in kids)
        if is_done(ef):
            pct = 100
        elif total:
            pct = round(sum(weight(k) for k in kids if is_done(k)) / total * 100)
        else:
            pct = text(ef.get(f["pct_complete"])) if f.get("pct_complete") else 0

        upcoming = sorted((k for k in kids if not is_done(k) and k.get("duedate")),
                          key=lambda k: k["duedate"])
        start = text(ef.get(f["start_date"])) if f.get("start_date") else ""
        rows.append({
            "project": ef.get("summary", e["key"]),
            "owner": text(ef.get("assignee")) or "Unassigned",
            "phase": text(ef.get(f["phase"])) if f.get("phase") else text(ef.get("status")),
            "start_date": start or (ef.get("created") or "")[:10],
            "due_date": ef.get("duedate") or "",
            "pct_complete": pct,
            "budget": text(ef.get(f["budget"])) if f.get("budget") else "",
            "spent": text(ef.get(f["spent"])) if f.get("spent") else "",
            "next_milestone": upcoming[0]["summary"] if upcoming else "",
            "milestone_date": upcoming[0]["duedate"] if upcoming else "",
            "key_risk": text(ef.get(f["key_risk"])) if f.get("key_risk") else "",
            "status_override": text(ef.get(f["rag_status"])) if f.get("rag_status") else "",
            "notes": text(ef.get(f["notes"])) if f.get("notes") else "",
            "jira_key": e["key"],
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-c", "--config", default=ROOT / "jira_config.json", type=Path)
    ap.add_argument("-o", "--output", default=ROOT / "data" / "jira_projects.csv", type=Path)
    ap.add_argument("--test", action="store_true", help="check connection and list matching epics")
    ap.add_argument("--list-fields", nargs="?", const="", metavar="SEARCH",
                    help="list Jira fields (optionally filtered by name)")
    args = ap.parse_args()

    if not args.config.exists():
        sys.exit(f"No config at {args.config}. Copy jira_config.example.json to jira_config.json and edit it.")
    cfg = json.loads(args.config.read_text())
    jira = Jira(cfg)

    if args.list_fields is not None:
        term = args.list_fields.lower()
        for fld in sorted(jira.fields(), key=lambda x: x["name"].lower()):
            if term in fld["name"].lower() or term in fld["id"].lower():
                print(f"{fld['id']:<22} {fld['name']:<35} {fld.get('schema', {}).get('type', '')}")
        return

    if args.test:
        epics = list(jira.search(cfg["jql"], ["summary", "status"]))
        print(f"Connected to {jira.base}. JQL matched {len(epics)} issue(s):")
        for e in epics[:25]:
            print(f"  {e['key']:<12} {text(e['fields'].get('status')):<15} {e['fields'].get('summary')}")
        return

    rows = pull(jira, cfg)
    if not rows:
        sys.exit("JQL matched no issues — run with --test and adjust 'jql' in the config.")
    args.output.parent.mkdir(exist_ok=True)
    with open(args.output, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        w.writerows(rows)
    print(f"Pulled {len(rows)} project(s) from Jira -> {args.output}")


if __name__ == "__main__":
    main()
