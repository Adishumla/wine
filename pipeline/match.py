"""Step 2: match every store's wines to Vivino. Only new or changed articles are matched.

- An override (state/overrides.csv) always wins: a Vivino id, or "none" for "no rating".
- A stored row is reused while the wine's name, vintage and the matcher version are unchanged; it keeps its
  `matched_at`. Everything else goes through matching.match (Algolia responses are cached, so re-matching after a
  matcher change costs no network for queries already asked).
- Wines not found on Vivino (band reject) are searched again with fresh Algolia responses, since new launches
  often aren't listed yet: weekly for the first two months after launch, then monthly (`searched_at`).
- New wines are cohort "p4"; rows from phase 2 are "p2" (state.py). The report and the audit work per assortment
  and cohort, and the build shows a new accept's rating once its group meets the precision bar.
- `limit` caps the wines matched in one run (widely carried wines first); the rest wait for the next run.
- Writes state/matches.csv (ids only; rows of wines no longer in any store stay), data/match_details.json (both
  sides' evidence, gitignored), data/review_queue.csv (the review band) and data/match.json (this run's counts).

Ratings come from ratings.refresh (step 3), not from here.
"""

from __future__ import annotations

import collections
import csv
import datetime
import time

from . import state, vivino
from .assortment import OUT
from .matching import MATCHER_VERSION, Candidate, Wine, match
from .net import DATA, Blocked, Http, load_json, save_json

RESEARCH_NEW_DAYS, RESEARCH_NEW_EVERY, RESEARCH_EVERY = 60, 7, 30
FRESH = 86400  # a re-search takes Algolia responses younger than this: a same-day rerun costs nothing
QUEUE_FIELDS = ["article", "assortment", "sb_name", "producer", "vintage", "country", "sb_origin", "sb_grapes",
                "sb_alcohol", "sb_packaging", "organic", "vivino_id", "vivino_name", "vivino_winery",
                "vivino_region", "vivino_country", "vivino_alcohol", "vivino_years", "ratings_count",
                "producer_score", "name_score", "total_score", "unconfirmed", "notes", "second_name",
                "second_winery", "second_total", "verdict", "note"]


def _candidate(c: Candidate | None) -> dict | None:
    if c is None:
        return None
    return {"vivino_id": c.vivino_id, "name": c.name, "winery": c.winery, "country": c.country,
            "type_id": c.type_id, "producer_score": c.producer_score, "name_score": c.name_score, "total": c.total,
            "contradictions": c.contradictions, "unconfirmed": c.unconfirmed, "notes": c.notes,
            "algolia_average": c.ratings_average, "algolia_count": c.ratings_count, **c.evidence}


def research_due(row: dict, launch: str | None, today: datetime.date) -> bool:
    """A reject is searched again weekly while its launch is under two months old, then monthly."""
    last = row.get("searched_at") or row.get("matched_at")
    if not last:
        return True
    try:
        age = (today - datetime.date.fromisoformat(launch[:10])).days if launch else None
    except ValueError:
        age = None
    every = RESEARCH_NEW_EVERY if age is not None and age <= RESEARCH_NEW_DAYS else RESEARCH_EVERY
    return (today - datetime.date.fromisoformat(last)).days >= every


def run(http: Http, limit: int | None = None, today: datetime.date | None = None) -> dict:
    wines = load_json(OUT)["wines"]
    today = today or datetime.date.today()
    now = today.isoformat()
    old = state.matches()
    overrides = state.overrides()
    old_details = load_json("match_details.json") if (DATA / "match_details.json").exists() else {}
    old_queue: dict[str, dict] = {}
    if (DATA / "review_queue.csv").exists():
        with (DATA / "review_queue.csv").open(newline="") as f:
            old_queue = {r["article"]: r for r in csv.DictReader(f)}

    rows: dict[str, dict] = {a: {**r, "cohort": r.get("cohort") or "p2"} for a, r in old.items()}
    details = dict(old_details)
    todo: list[tuple[dict, Wine, str]] = []  # (product, wine, why): "new", "changed" or "research"
    kept = 0
    for p in wines.values():
        w = Wine.from_sb(p)
        prev = rows.get(w.article)
        ov = overrides.get(w.article)
        if ov:
            vid = ov["vivino_id"].strip()
            row = {**_base(p, w), "vivino_id": "" if vid.lower() == "none" else vid, "band": "override",
                   "cohort": prev["cohort"] if prev else "p4"}
            same = prev and all(prev.get(k) == str(row.get(k, "")) for k in ("vivino_id", "band", "sb_name", "sb_vintage"))
            row["matched_at"] = prev["matched_at"] if same else now
            row["searched_at"] = prev.get("searched_at", "") if prev else ""
            rows[w.article] = row
            continue
        unchanged = prev and prev["band"] != "override" and all(
            prev.get(k) == v for k, v in (("sb_name", w.name), ("sb_vintage", w.vintage),
                                          ("matcher_version", MATCHER_VERSION)))
        if not unchanged:
            todo.append((p, w, "changed" if prev else "new"))
        elif prev["band"] == "reject" and research_due(prev, p.get("productLaunchDate"), today):
            todo.append((p, w, "research"))
        else:
            kept += 1
    # Widely carried wines first, then newest launches; re-searches after first-time matches.
    todo.sort(key=lambda t: (t[2] == "research", -(t[0].get("ns") or 0), t[0].get("productLaunchDate") or ""))
    left = todo[limit:] if limit is not None else []
    todo = todo[:limit] if limit is not None else todo

    creds = vivino.algolia_credentials(http) if todo else None
    queries = 0
    fresh = False

    def search(q: str) -> list[dict]:
        nonlocal queries
        queries += 1
        return vivino.search(http, creds, q, max_age=FRESH if fresh else None)

    res: dict = {"wines": len(wines), "kept": kept, "matcher_version": MATCHER_VERSION}
    done: collections.Counter = collections.Counter()
    by_group: dict[str, collections.Counter] = collections.defaultdict(collections.Counter)
    try:
        for p, w, why in todo:
            fresh = why == "research"
            r = match(w, search)
            b = r["best"]
            prev = rows.get(w.article)
            row = {**_base(p, w), "vivino_id": str(b.vivino_id) if b and r["band"] != "reject" else "",
                   "band": r["band"], "producer_score": b.producer_score if b else "",
                   "name_score": b.name_score if b else "", "total_score": b.total if b else "",
                   "cohort": prev["cohort"] if prev else "p4", "searched_at": now}
            same = prev and all(prev.get(k) == str(row.get(k, "")) for k in
                                ("vivino_id", "band", "sb_name", "sb_vintage", "matcher_version"))
            row["matched_at"] = prev["matched_at"] if same else now
            rows[w.article] = row
            details[w.article] = {"band": r["band"], "queries": r["queries"], "best": _candidate(b),
                                  "second": _candidate(r["second"])}
            done[why] += 1
            if why == "new":
                by_group[row["assortment"]][r["band"]] += 1
            elif why == "research" and r["band"] != "reject":
                done["found_on_research"] += 1
    except Blocked as e:
        res["blocked"] = str(e)  # what was matched is kept; the rest keep their previous rows or wait

    state.write_matches(list(rows.values()))
    save_json("match_details.json", details)
    queue = [_queue_row(p, Wine.from_sb(p), details.get(str(p["productNumber"])), old_queue)
             for p in wines.values() if rows.get(str(p["productNumber"]), {}).get("band") == "review"]
    with (DATA / "review_queue.csv").open("w", newline="") as f:
        wr = csv.DictWriter(f, fieldnames=QUEUE_FIELDS)
        wr.writeheader()
        wr.writerows(q for q in queue if q)
    current = [rows[str(p["productNumber"])] for p in wines.values() if str(p["productNumber"]) in rows]
    res |= {"matched": dict(done), "left_for_next_run": len(left), "algolia_queries": queries,
            "new_by_assortment": {g: dict(c) for g, c in sorted(by_group.items())},
            "bands_current": dict(collections.Counter(r["band"] for r in current)),
            "unmatched_current": len(wines) - len(current), "rows": len(rows), "at": time.time()}
    save_json("match.json", res)
    return res


def _base(p: dict, w: Wine) -> dict:
    return {"article": w.article, "product_id": w.product_id, "assortment": p.get("assortmentText") or "",
            "sb_name": w.name, "sb_vintage": w.vintage, "matcher_version": MATCHER_VERSION}


def _queue_row(p: dict, w: Wine, d: dict | None, old_queue: dict[str, dict]) -> dict | None:
    """A review-queue row from the stored evidence, keeping any verdict already written in the old queue."""
    if not d:
        return old_queue.get(w.article)
    b, s = d.get("best") or {}, d.get("second") or {}
    prev = old_queue.get(w.article, {})
    same = str(prev.get("vivino_id", "")) == str(b.get("vivino_id", ""))
    return {
        "article": w.article, "assortment": p.get("assortmentText") or "", "sb_name": w.name, "producer": w.producer,
        "vintage": w.vintage, "country": w.country,
        "sb_origin": " / ".join(x for x in (p.get("originLevel1"), p.get("originLevel2")) if x),
        "sb_grapes": ", ".join(g for g in p.get("grapes") or [] if isinstance(g, str)),
        "sb_alcohol": p.get("alcoholPercentage") or "", "sb_packaging": p.get("packagingLevel1") or "",
        "organic": p.get("isOrganic"),
        "vivino_id": b.get("vivino_id", ""), "vivino_name": b.get("name", ""), "vivino_winery": b.get("winery", ""),
        "vivino_region": b.get("region", ""), "vivino_country": b.get("country", ""),
        "vivino_alcohol": b.get("alcohol", ""), "vivino_years": b.get("years", ""),
        "ratings_count": b.get("algolia_count", ""), "producer_score": b.get("producer_score", ""),
        "name_score": b.get("name_score", ""), "total_score": b.get("total", ""),
        "unconfirmed": "; ".join(b.get("unconfirmed") or []), "notes": "; ".join(b.get("notes") or []),
        "second_name": s.get("name", ""), "second_winery": s.get("winery", ""), "second_total": s.get("total", ""),
        "verdict": prev.get("verdict", "") if same else "", "note": prev.get("note", "") if same else "",
    }
