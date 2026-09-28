"""Review loop, step 1: "Wrong match?" reports from the app, filed as issues in this private repo.

The app opens a prefilled new issue: title "Match: <article> <name>", and a body with the article, the Vivino wine
it's matched to, a line for what's wrong and one for the right wine. `sync()` runs on the Mac, where `gh` is
signed in (the nightly's deploy key can't read issues), and turns open reports into state:

- A report is Adam's verdict: the reported pairing gets a "wrong" label, and the build shows the wine as
  "reported" (no rating) until the report is resolved.
- A Vivino link in the report (…/w/{id}; or …/wines/{id}, a vintage id, looked up on api.vivino.com) is his
  override, and "none" or "not on Vivino" an override to no rating. Either resolves the report.
- Without one, the wine heads the review queue (queue.py), whose answer resolves it: "fixed" when an override or
  another checked wine replaces the match, "kept" when the match is checked right after all.
- An issue Adam closes himself before it's resolved withdraws the report and its "wrong" label.

`close=True` closes the issues of resolved reports with a short comment. Only counts are printed.
"""

from __future__ import annotations

import collections
import json
import re
import subprocess
import time
from typing import Any, Callable

from . import state, vivino
from .net import Http

TITLE = re.compile(r"^\s*Match:\s*(\d{3,})")
FACTS = ("article", "wine", "matched to")  # template lines holding facts, not Adam's answer
ANSWERS = ("what's wrong", "what is wrong", "right wine")  # template headers; the answer follows the colon
ARTICLE = re.compile(r"^\s*Article:\s*(\d{3,})", re.M)
MATCHED = re.compile(r"^\s*Matched to:.*?vivino\.com/\S*?\bw/(\d+)", re.M)
WINE_LINK = re.compile(r"vivino\.com/\S*?\bw/(\d+)")
VINTAGE_LINK = re.compile(r"vivino\.com/\S*?\bwines/(\d+)")
BARE_ID = re.compile(r"^\s*(\d{3,})\s*$", re.M)
NO_WINE = re.compile(r"\bnone\b|not on vivino|inte på vivino", re.I)


def note(issue: str) -> str:
    return f"reported in the app (issue #{issue})"


def parse(issue: dict) -> dict | None:
    """A report from an issue, or None when it isn't one. suggested: a wine id, "vintage:<id>", "none" or ""."""
    m = TITLE.match(issue.get("title") or "")
    if not m:
        return None
    body = (issue.get("body") or "").replace("\r\n", "\n")
    a = ARTICLE.search(body)
    matched = MATCHED.search(body)
    answer, right = [], []  # all of Adam's text; what's under "Right wine", where a bare id or "none" counts
    for line in body.split("\n"):
        head, sep, rest = line.partition(":")
        key = head.strip().lower().replace("’", "'")
        if sep and key.startswith(FACTS):
            continue
        if sep and key.startswith(ANSWERS):
            line = rest
            right = [] if key.startswith("right wine") else None
        answer.append(line)
        if right is not None:
            right.append(line)
    text, right_text = "\n".join(answer), "\n".join(right or [])
    w, v, bare = WINE_LINK.search(text), VINTAGE_LINK.search(text), BARE_ID.search(right_text)
    suggested = (w.group(1) if w else f"vintage:{v.group(1)}" if v else bare.group(1) if bare
                 else "none" if NO_WINE.search(right_text) else "")
    return {"issue": str(issue["number"]), "article": a.group(1) if a else m.group(1),
            "vivino_id": matched.group(1) if matched else "", "suggested": suggested,
            "reported_at": (issue.get("createdAt") or time.strftime("%Y-%m-%d"))[:10]}


def resolve(rows: dict[str, dict] | None = None, today: str | None = None) -> dict[str, dict]:
    """Move open reports to fixed or kept from the current overrides and labels; writes reports.csv."""
    rows = state.reports() if rows is None else rows
    ov, lab, m = state.overrides(), state.labels(), state.matches()
    today = today or time.strftime("%Y-%m-%d")
    for r in rows.values():
        if r["status"] != "open":
            continue
        a, vid = r["article"], r["vivino_id"]
        o = (ov.get(a) or {}).get("vivino_id")
        current = (m.get(a) or {}).get("vivino_id") or ""
        if vid and (o == vid or lab.get((a, vid), {}).get("verdict") == "right"):
            r["status"] = "kept"
        elif o or (current and current != vid and lab.get((a, current), {}).get("verdict") == "right"):
            r["status"] = "fixed"
        if r["status"] != "open":
            r["resolved_at"] = today
    if rows:
        state.write_reports(list(rows.values()))
    return rows


def _gh(args: list[str]) -> Any:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, check=True, timeout=120).stdout
    return json.loads(out) if out.strip()[:1] in ("[", "{") else out


def sync(close: bool = False, gh: Callable[[list[str]], Any] = _gh, http: Http | None = None) -> dict:
    slug = state.repo()
    if not slug:
        raise RuntimeError("state/ has no GitHub remote")
    issues = gh(["issue", "list", "-R", slug, "--state", "open", "--limit", "1000",
                 "--json", "number,title,body,createdAt"])
    rows = state.reports()
    counts: collections.Counter = collections.Counter()
    open_now: set[str] = set()
    labels, overrides = [], []
    for issue in issues:
        p = parse(issue)
        if not p:
            continue
        open_now.add(p["issue"])
        if p["issue"] in rows:
            continue  # imported before; the queue moves it on
        counts["new"] += 1
        sug = p.pop("suggested")
        if sug.startswith("vintage:"):
            wid = vivino.wine_of_vintage(http or Http("reports"), int(sug.split(":")[1]))
            sug = str(wid) if wid else ""
            counts["links_not_resolved"] += not wid
        a, vid, n = p["article"], p["vivino_id"], note(p["issue"])
        if vid and sug != vid:
            labels.append({"article": a, "vivino_id": vid, "verdict": "wrong", "note": n})
        if sug:
            overrides.append({"article": a, "vivino_id": sug, "note": f"Adam, in the app (issue #{p['issue']})"})
            if sug != "none":
                labels.append({"article": a, "vivino_id": sug, "verdict": "right", "note": n})
        rows[p["issue"]] = {**p, "suggested_id": sug, "status": "open", "resolved_at": ""}
    state.add_labels(labels)
    state.set_overrides(overrides)
    for n, r in rows.items():  # closed on GitHub before it was resolved: withdrawn
        if r["status"] == "open" and n not in open_now:
            r["status"], r["resolved_at"] = "withdrawn", time.strftime("%Y-%m-%d")
            state.drop_labels({(r["article"], r["vivino_id"])}, note(n))
    rows = resolve(rows)
    counts.update(r["status"] for r in rows.values())
    if close:
        for n, r in rows.items():
            if n in open_now and r["status"] in ("fixed", "kept"):
                msg = ("Fixed: the new match or \"no rating\" reaches the app with the next nightly."
                       if r["status"] == "fixed" else
                       "Checked: the match is right, so its rating shows again from the next nightly.")
                gh(["issue", "close", n, "-R", slug, "--comment", msg])
                counts["closed"] += 1
    return {"open_issues": len(open_now), **counts}
