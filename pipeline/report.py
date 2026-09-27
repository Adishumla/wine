"""Step 3: coverage and precision per group, from state/matches.csv and state/labels.csv.

A group is an assortment within an audit cohort (state.py): "Fast sortiment" is phase 2's audited wines,
"Fast sortiment (new)" the wines first matched from phase 4 on. Precision uses Adam's labels on accepted pairs
only: exact one-sided 95 % lower bound (Clopper-Pearson), unsure counted as wrong. A group with fewer accepts than
the sample size needs is checked in full instead. Writes data/report.md.
"""

from __future__ import annotations

import collections
import json
import math
import time

from . import state
from .net import DATA, load_json

TARGET = 0.95  # phase 2: every assortment group's accept precision >= 95 % at a 95 % lower bound


def lower_bound(right: int, n: int, confidence: float = 0.95) -> float | None:
    if n == 0:
        return None
    if right == 0:
        return 0.0

    def tail(p: float) -> float:  # P(X >= right | n, p)
        return sum(math.comb(n, k) * p**k * (1 - p) ** (n - k) for k in range(right, n + 1))

    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = (lo + hi) / 2
        lo, hi = (mid, hi) if tail(mid) < 1 - confidence else (lo, mid)
    return round(lo, 4)


def needed(wrong: int, target: float = TARGET) -> int:
    """Smallest number of labelled accepts that supports `target` with this many wrong (or unsure)."""
    n = wrong + 1
    while (lower_bound(n - wrong, n) or 0) < target:
        n += 1
    return n


def group_of(row: dict) -> str:
    return row["assortment"] + state.COHORTS[row.get("cohort") or "p2"]


def groups() -> dict[str, dict]:
    rows = list(state.matches().values())
    labels = state.labels()
    out: dict[str, dict] = {}
    for g in sorted({group_of(r) for r in rows}):
        rs = [r for r in rows if group_of(r) == g]
        bands = collections.Counter(r["band"] for r in rs)
        lab = [labels[(r["article"], r["vivino_id"])]["verdict"] for r in rs
               if r["band"] == "accept" and (r["article"], r["vivino_id"]) in labels]
        c = collections.Counter(lab)
        bad = c["wrong"] + c["unsure"]
        accepts = bands.get("accept", 0)
        # A group too small to reach the sample size is checked in full (a census): then precision is known
        # exactly, and the target is met when every accept is right.
        census = accepts < needed(bad)
        out[g] = {"wines": len(rs), **{b: bands.get(b, 0) for b in ("accept", "review", "reject", "override")},
                  "labelled_accepts": len(lab), "right": c["right"], "wrong": c["wrong"], "unsure": c["unsure"],
                  "lower_95": lower_bound(c["right"], len(lab)), "census": census,
                  "more_to_check": (accepts - len(lab)) if census else max(0, needed(bad) - len(lab))}
        g_ = out[g]
        if not accepts:
            g_["meets_target"] = None  # no accepted ratings to verify
        elif census:
            g_["meets_target"] = len(lab) == accepts and bad == 0
        else:
            g_["meets_target"] = g_["lower_95"] is not None and g_["lower_95"] >= TARGET
    return out


def write() -> str:
    gs = groups()
    meta = load_json("match.json") if (DATA / "match.json").exists() else {}
    ratings = load_json("ratings.json") if (DATA / "ratings.json").exists() else {}
    lines = ["# Matching: every store's wines", "", f"Generated {time.strftime('%Y-%m-%d %H:%M')}; "
             f"matcher {meta.get('matcher_version', '?')}; last match run: {json.dumps(meta.get('matched', {}))}, "
             f"{meta.get('algolia_queries', '?')} Algolia queries, {meta.get('left_for_next_run', 0)} left for the "
             f"next run; {len(ratings)} ratings cached.", "",
             f"New wines in the last run, by band: {json.dumps(meta.get('new_by_assortment', {}), ensure_ascii=False)}",
             "",
             "| Assortment | Wines | Accept | Review | Reject | Override | Accept % | Labelled accepts | Right | "
             "Wrong | Unsure | ≥ (95 %) | Meets 95 % | To check |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    tot = collections.Counter()
    for g, v in gs.items():
        for k in ("wines", "accept", "review", "reject", "override"):
            tot[k] += v[k]
        bound = "" if v["lower_95"] is None else f"{100 * v['lower_95']:.1f} %"
        if v["census"] and v["labelled_accepts"] == v["accept"] and v["accept"]:
            bound = f"all {v['accept']} checked"
        lines.append(f"| {g or '(none)'} | {v['wines']} | {v['accept']} | {v['review']} | {v['reject']} | "
                     f"{v['override']} | {100 * v['accept'] / v['wines']:.0f} % | {v['labelled_accepts']} | "
                     f"{v['right']} | {v['wrong']} | {v['unsure']} | {bound} | "
                     f"{ {True: 'yes', False: 'no', None: 'n/a'}[v['meets_target']] } | {v['more_to_check']} |")
    lines.append(f"| **All** | {tot['wines']} | {tot['accept']} | {tot['review']} | {tot['reject']} | "
                 f"{tot['override']} | {100 * tot['accept'] / max(1, tot['wines']):.0f} % | | | | | | | |")
    text = "\n".join(lines) + "\n"
    (DATA / "report.md").write_text(text)
    return text
