"""Step 3 (phase 4): rolling Vivino rating refresh by id, low counts first (docs/plan.md, Ratings over time).

Which ids: every Vivino id whose rating the app shows, by build.py's rule: the matcher's accept rows and the
overrides with an id (an override always wins for its article; "none" means no id). Ids no longer matched keep
their entry in data/ratings.json but aren't refreshed.

When an id is due, from its last successful check (`checked_at`):

    never fetched                                              now
    no rating yet (average 0/None: below Vivino's threshold)   every 2 days
    < 100 ratings                                              every 3 days
    100-1,000                                                  every 7 days
    > 1,000                                                    every 30 days

Order: never fetched first, then by rating count ascending (no rating = 0), then oldest check. At most `limit`
lookups reach the network per run (cache hits are free); the rest wait for the next run.

- api.vivino.com responses are cached for 12 h: a rerun the same day reuses what a killed run already fetched,
  and a due refresh on a later day always goes to the network.
- A failed lookup (not 200, or no statistics) keeps the last value and its `checked_at`, and records `tried_at`
  and `failures`. The id is tried again 1, 2, 4 ... 30 days later, so a wine gone from Vivino costs about one
  request a month; a success clears the failures.
- net.Blocked (repeated 403s or 429s) or MAX_SERVER_ERRORS 5xx/network errors in a row stop the run; everything
  refreshed so far is kept.

Writes data/ratings.json ({id: {average, count, checked_at[, tried_at, failures]}}; checked_at None = never
fetched successfully), atomically every SAVE_EVERY lookups and at the end, and the run's stats to
data/refresh.json. Ratings are Vivino data: data/ only, never state/.
"""

from __future__ import annotations

import collections
import json
import time
from datetime import date, timedelta
from typing import Any

import httpx

from . import state, vivino
from .net import DATA, Blocked, Http, group_stats, policy_for

NAME, STATS = "ratings.json", "refresh.json"
MAX_AGE = 12 * 3600
INTERVALS = {"no_rating": 2, "under_100": 3, "100_1000": 7, "over_1000": 30}  # days between checks
TIERS = ("new", *INTERVALS)
MAX_BACKOFF = 30  # days: a failing id is tried again after 1, 2, 4 ... 30 days
SAVE_EVERY = 50
MAX_SERVER_ERRORS = 5  # in a row, each already retried by Http: Vivino is down, don't spend the run on it
RATED_BANDS = ("accept", "override")  # as build.py, which also rates a review match checked right ("checked")


def rated_ids() -> tuple[set[str], list[str]]:
    """Vivino ids the app shows a rating for (accepts, overrides, and matches checked right by hand whatever their
    band), and any id that isn't a number (a typo in overrides.csv)."""
    matches, overrides, labels = state.matches(), state.overrides(), state.labels()
    ids, bad = set(), []
    for art in matches.keys() | overrides.keys():
        ov = overrides.get(art)
        band = "override" if ov else matches[art].get("band", "")
        vid = ((ov or matches[art]).get("vivino_id") or "").strip()
        checked = labels.get((art, vid), {}).get("verdict") == "right"
        if (band not in RATED_BANDS and not checked) or not vid or vid.lower() == "none":
            continue
        if vid.isdigit():
            ids.add(vid)
        else:
            bad.append(vid)
    return ids, sorted(bad)


def tier(r: dict | None) -> str:
    if not r or not r.get("checked_at"):
        return "new"
    if not r.get("average"):
        return "no_rating"
    n = r.get("count") or 0
    return "under_100" if n < 100 else "100_1000" if n <= 1000 else "over_1000"


def scheduled(r: dict | None, day: date) -> bool:
    t = tier(r)
    return t == "new" or date.fromisoformat(r["checked_at"]) + timedelta(days=INTERVALS[t]) <= day


def backing_off(r: dict | None, day: date) -> bool:
    n = (r or {}).get("failures") or 0
    return bool(n and r.get("tried_at")) and \
        date.fromisoformat(r["tried_at"]) + timedelta(days=min(2 ** (n - 1), MAX_BACKOFF)) > day


def due(r: dict | None, day: date) -> bool:
    return scheduled(r, day) and not backing_off(r, day)


def plan(ids: set[str], ratings: dict, day: date) -> tuple[list[str], dict]:
    """The due ids in refresh order, and counts per tier."""
    counts: dict[str, Any] = {"ids": len(ids), "never_fetched": 0, "due": dict.fromkeys(TIERS, 0),
                              "not_due": 0, "backing_off": 0}
    queue = []
    for vid in ids:
        r = ratings.get(vid)
        t = tier(r)
        counts["never_fetched"] += t == "new"
        if not scheduled(r, day):
            counts["not_due"] += 1
        elif backing_off(r, day):
            counts["backing_off"] += 1
        else:
            counts["due"][t] += 1
            queue.append(vid)

    def key(vid: str) -> tuple:
        r = ratings.get(vid) or {}
        count = (r.get("count") or 0) if r.get("average") else 0
        return tier(r) != "new", count, r.get("checked_at") or "", int(vid)

    return sorted(queue, key=key), counts


def _save(name: str, data: Any) -> None:
    path = DATA / name
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    tmp.replace(path)  # a killed run never leaves a half-written file


def _sent() -> int:
    """Requests the Vivino host group has sent in this process, retries included."""
    return group_stats().get(policy_for("api.vivino.com").group, {}).get("requests", 0)


def refresh(http: Http, limit: int = 600, today: str | None = None) -> dict:
    t0 = time.time()
    day = date.fromisoformat(today) if today else date.today()
    checked = day.isoformat()
    ids, bad = rated_ids()
    path = DATA / NAME
    ratings: dict = json.loads(path.read_text()) if path.exists() else {}
    queue, counts = plan(ids, ratings, day)
    stats: dict[str, Any] = {"today": checked, **counts, "unmatched_kept": len(ratings.keys() - ids), "limit": limit}
    if bad:
        stats["bad_ids"] = bad
    failed: collections.Counter = collections.Counter()
    looked = sent = refreshed = hits = errors = 0
    start = _sent()
    try:
        for vid in queue:
            if sent >= limit:
                break
            before = _sent()
            try:
                status, body = vivino.wine(http, int(vid), max_age=MAX_AGE)
            except httpx.HTTPError:
                status, body = 0, None  # network error after Http's retries
            looked += 1
            if _sent() > before:
                sent += 1
            else:
                hits += 1
            body = body if isinstance(body, dict) else {}
            st = (body.get("wine", body) or {}).get("statistics") or {}
            if status == 200 and st:
                ratings[vid] = {"average": st.get("ratings_average") or None, "count": st.get("ratings_count"),
                                "checked_at": checked}
                refreshed += 1
                errors = 0
            else:  # keep the last value and its date
                old = ratings.get(vid) or {"average": None, "count": None, "checked_at": None}
                ratings[vid] = {**old, "tried_at": checked, "failures": (old.get("failures") or 0) + 1}
                failed["no statistics" if status == 200 else str(status) if status else "network"] += 1
                errors = errors + 1 if not status or status >= 500 else 0
                if errors >= MAX_SERVER_ERRORS:
                    stats["stopped"] = f"{errors} server errors in a row (last: HTTP {status or 'network error'})"
                    break
            if looked % SAVE_EVERY == 0:
                _save(NAME, ratings)
                print(f"ratings: {looked}/{len(queue)} looked up, {refreshed} refreshed, "
                      f"{sum(failed.values())} failed", flush=True)
    except Blocked as e:
        stats["blocked"] = str(e)
    finally:
        _save(NAME, ratings)
    stats.update(looked_up=looked, refreshed=refreshed, failed=sum(failed.values()), failed_by_status=dict(failed),
                 left=len(queue) - looked, requests=_sent() - start, cache_hits=hits,
                 seconds=round(time.time() - t0, 1))
    _save(STATS, stats)
    return stats
