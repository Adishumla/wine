"""Tracked state in state/ (committed to the private repo): matches, overrides and hand labels.

Vivino ids and verdicts only, never Vivino names or ratings (those stay in data/, which is gitignored).

- matches.csv: one row per Systembolaget article, what the matcher (or an override) decided, when it was last
  searched on Vivino, and its audit cohort. Rows of wines no longer in any store stay, so they aren't re-matched.
- overrides.csv: article -> Vivino id, or "none" for "no rating". Always wins over the matcher.
- labels.csv: Adam's verdicts on (article, Vivino id) pairs: right / wrong / unsure. The only ground truth (with
  checks he explicitly delegates, marked in the note).
- reports.csv: "Wrong match?" reports from the app, one per issue in this repo: which pairing, the Vivino id Adam
  pasted if any, and whether it's open, fixed (an override or a checked match replaced it) or kept (checked right
  after all). Ids only: Adam's words stay in the issue, since they may name a Vivino wine.
"""

from __future__ import annotations

import csv
import re
import subprocess
import time
from pathlib import Path

from .net import STATE

MATCH_FIELDS = ["article", "product_id", "assortment", "sb_name", "sb_vintage", "vivino_id", "band",
                "producer_score", "name_score", "total_score", "matcher_version", "matched_at", "searched_at", "cohort"]
# Rows matched and audited in phase 2 (store 1208's wines) are cohort "p2"; store wines first matched later are
# "p4", order-only wines no store carried when first matched (phase 7) "p7". The precision bar applies per
# assortment and cohort: an audit says nothing about wines it never saw.
COHORTS = {"p2": "", "p4": " (new)", "p7": " (order-only)"}
OVERRIDE_FIELDS = ["article", "vivino_id", "note", "set_at"]
LABEL_FIELDS = ["article", "vivino_id", "verdict", "note", "labelled_at"]
REPORT_FIELDS = ["issue", "article", "vivino_id", "suggested_id", "reported_at", "status", "resolved_at"]
VERDICTS = {"right": "right", "r": "right", "ok": "right", "yes": "right", "correct": "right",
            "wrong": "wrong", "w": "wrong", "no": "wrong", "incorrect": "wrong",
            "unsure": "unsure", "u": "unsure", "?": "unsure", "unresolved": "unsure"}


def _path(name: str) -> Path:
    return STATE / name


def _read(name: str) -> list[dict]:
    path = _path(name)
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def _write(name: str, fields: list[str], rows: list[dict], key=lambda r: r["article"]) -> None:
    STATE.mkdir(parents=True, exist_ok=True)
    tmp = _path(name + ".tmp")
    with tmp.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n", extrasaction="ignore")
        w.writeheader()
        w.writerows(sorted(rows, key=key))
    tmp.replace(_path(name))  # never leave a half-written state file


def matches() -> dict[str, dict]:
    return {r["article"]: r for r in _read("matches.csv")}


def write_matches(rows: list[dict]) -> None:
    _write("matches.csv", MATCH_FIELDS, rows)


def overrides() -> dict[str, dict]:
    """article -> {"vivino_id": id or "none", ...}."""
    return {r["article"]: r for r in _read("overrides.csv") if r.get("article")}


def labels() -> dict[tuple[str, str], dict]:
    return {(r["article"], r["vivino_id"]): r for r in _read("labels.csv")}


def add_labels(rows: list[dict]) -> int:
    """Merge verdicts into labels.csv. Returns how many were new or changed."""
    current = labels()
    changed = 0
    for r in rows:
        verdict = VERDICTS.get((r.get("verdict") or "").strip().lower())
        if not verdict or not r.get("article") or not r.get("vivino_id"):
            raise ValueError(f"bad label row: {r}")
        key = (r["article"], r["vivino_id"])
        note = (r.get("note") or "").strip()
        old = current.get(key)
        if old and old["verdict"] == verdict and old["note"] == note:
            continue
        current[key] = {"article": key[0], "vivino_id": key[1], "verdict": verdict, "note": note,
                        "labelled_at": time.strftime("%Y-%m-%d")}
        changed += 1
    if changed:
        _write("labels.csv", LABEL_FIELDS, list(current.values()), key=lambda r: (r["article"], r["vivino_id"]))
    return changed


def set_overrides(rows: list[dict]) -> int:
    """Merge {article, vivino_id (an id or "none"), note} into overrides.csv. Returns how many were new or changed."""
    current = overrides()
    changed = 0
    for r in rows:
        vid = (r.get("vivino_id") or "").strip().lower()
        if not r.get("article") or not (vid.isdigit() or vid == "none"):
            raise ValueError(f"bad override row: {r}")
        old = current.get(r["article"])
        if old and old["vivino_id"] == vid:
            continue
        current[r["article"]] = {"article": r["article"], "vivino_id": vid, "note": (r.get("note") or "").strip(),
                                 "set_at": time.strftime("%Y-%m-%d")}
        changed += 1
    if changed:
        _write("overrides.csv", OVERRIDE_FIELDS, list(current.values()))
    return changed


def reports() -> dict[str, dict]:
    """issue number -> report row."""
    return {r["issue"]: r for r in _read("reports.csv") if r.get("issue")}


def write_reports(rows: list[dict]) -> None:
    _write("reports.csv", REPORT_FIELDS, rows, key=lambda r: int(r["issue"]))


def drop_labels(keys: set[tuple[str, str]], note: str) -> int:
    """Remove labels on these pairings that still carry exactly this note (a withdrawn report's)."""
    current = labels()
    gone = [k for k in keys if k in current and current[k]["note"] == note]
    for k in gone:
        del current[k]
    if gone:
        _write("labels.csv", LABEL_FIELDS, list(current.values()), key=lambda r: (r["article"], r["vivino_id"]))
    return len(gone)


def repo() -> str | None:
    """owner/name of the GitHub repo holding state/, from its git remote."""
    try:
        url = subprocess.run(["git", "-C", str(STATE), "remote", "get-url", "origin"], capture_output=True, text=True,
                             timeout=10).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$", url)
    return m.group(1) if m else None
