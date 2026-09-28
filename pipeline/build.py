"""Build step: compact data files for the app in data/app/, from local state only (no network).

Inputs: data/assortment.json (every store's wines with stock and shelf, and the store list), data/orders.json (the
order-only range), state/matches.csv + state/overrides.csv (what each wine is on Vivino) and data/ratings.json
(ratings by Vivino id).

Outputs (gitignored, they hold Vivino ratings):
- wines.json: one compact row per wine; repeated strings as lookup tables; the adjusted rating precomputed
  (docs/plan.md, Ranking). A rating only for an override, an accept whose group meets the precision bar
  (report.py), or a match checked right by hand (band "checked" when the matcher only had it in review); any
  other accept goes out as band "unchecked", with no rating. A match reported wrong and not fixed yet is band
  "reported", with no rating.
- avail/<storeId>.json: [wine index, stock, shelf] for every wine the store carries, so a phone downloads one
  store (tens of kB), not all of them. Stock null = in the store's assortment but no stock row yet (upcoming
  launches, order-only wines not delivered). avail/orders.json: every wine in the order-only range, the
  store picker's "Order only" (no stock or shelf: ordered to any store).
- stores.json: id, name, town, address, position, wine count, and the date its list was checked; "stale" when
  the last fetch didn't add up and the store kept its previous list, "partial" when there was no previous list
  and today's is known to be incomplete.
- meta.json: schema version, build time, counts, rating and stock dates, and where "Wrong match?" files its
  report: new issues in this private repo (from the state checkout's git remote).
"""

from __future__ import annotations

import collections
import json
import time

from . import orders, state
from .assortment import OUT
from .net import DATA, load_json
from .report import group_of, groups

SCHEMA = 1
PRIOR_MEAN = 3.9  # C: in-store median rating
PRIOR_WEIGHT = 100  # m: ratings worth of pull toward C
RATED_BANDS = ("accept", "override", "checked")


def adjusted(avg: float, count: int) -> float:
    """(n·r + m·C) / (n + m): a 4.5 from 12 ratings doesn't outrank a 4.3 from 20,000."""
    return round((count * avg + PRIOR_WEIGHT * PRIOR_MEAN) / (count + PRIOR_WEIGHT), 3)


VALUE_BAND = 0.15  # peers: same type, price per 75 cl within ±15 %
VALUE_MIN_PEERS = 5


def values(wines: list[dict]) -> None:
    """Value (docs/plan.md, Ranking): the adjusted rating minus the median adjusted rating of rated wines of the same
    type within ±15 % of its price per 75 cl. Set as "val" on rated wines with at least VALUE_MIN_PEERS peers."""
    def p75(w: dict) -> float | None:
        return w["price"] * 750 / w["vol"] if w.get("price") and w.get("vol") else None

    rated = [(w, p75(w)) for w in wines if w.get("adj") is not None and p75(w)]
    by_type: dict = {}
    for w, p in rated:
        by_type.setdefault(w["ty"], []).append((p, w["adj"]))
    for group in by_type.values():
        group.sort()
    for w, p in rated:
        group = by_type[w["ty"]]
        lo, hi = p * (1 - VALUE_BAND), p * (1 + VALUE_BAND)
        peers = sorted(a for q, a in group if lo <= q <= hi)
        if len(peers) - 1 < VALUE_MIN_PEERS:  # the wine itself is among them
            continue
        n = len(peers)
        median = peers[n // 2] if n % 2 else (peers[n // 2 - 1] + peers[n // 2]) / 2
        w["val"] = round(w["adj"] - median, 3)


# A wine row leaves out fields at these values (most of the order-only range has no match, rating or photo yet);
# expand() and the app's data.js put them back. Whole-number floats are written as integers (750.0 -> 750).
ROW_DEFAULTS = {"thin": None, "prod": None, "vint": None, "abv": None, "sugar": None, "r": None, "st": None, "g": [],
                "org": 0, "new": None, "viv": None, "band": "", "rat": None, "cnt": None, "adj": None, "rchk": None,
                "ns": 0, "img": 0}


def compact(row: dict) -> dict:
    return {k: int(v) if isinstance(v, float) and v.is_integer() else v for k, v in row.items()
            if not (k in ROW_DEFAULTS and v == ROW_DEFAULTS[k] and type(v) is type(ROW_DEFAULTS[k]))}


def expand(row: dict) -> dict:
    return {**ROW_DEFAULTS, **row}


class Lookup:
    """Repeated strings as indices into a list."""

    def __init__(self) -> None:
        self.values: list[str] = []
        self._index: dict[str, int] = {}

    def __call__(self, value: str | None) -> int | None:
        if not value:
            return None
        if value not in self._index:
            self._index[value] = len(self.values)
            self.values.append(value)
        return self._index[value]


def store_name(s: dict) -> str:
    """Most stores' alias is just their number; those go by their street address."""
    alias = (s.get("alias") or "").strip()
    return alias if alias and not alias.isdigit() else (s.get("address") or str(s["siteId"]))


def build() -> dict:
    src = load_json(OUT)
    matches = state.matches()
    overrides = state.overrides()
    labels = state.labels()
    confirmed_groups = {g for g, v in groups().items() if v["meets_target"]}
    reported = {(r["article"], r["vivino_id"]) for r in state.reports().values() if r["status"] == "open" and r["vivino_id"]}
    ratings = load_json("ratings.json") if (DATA / "ratings.json").exists() else {}
    looks = {k: Lookup() for k in ("country", "region", "type", "style", "grape", "assortment", "packaging")}

    wines, index = [], {}
    for pid, p in orders.catalog().items():
        art = str(p["productNumber"])
        m = matches.get(art, {})
        ov = overrides.get(art)
        band = "override" if ov else m.get("band", "")
        vid = (ov["vivino_id"] if ov else m.get("vivino_id", "")) or ""
        vid = "" if vid.lower() == "none" or not vid.strip().isdigit() else vid.strip()  # a typo in overrides: no id
        verdict = labels.get((art, vid), {}).get("verdict") if vid else None
        if band != "override" and (art, vid) in reported:
            band = "reported"  # Adam reported it wrong; the queue finds the right wine
        elif band == "accept":
            if verdict in ("wrong", "unsure"):
                band = "review"  # checked and not confirmed, and no override set yet
            elif verdict != "right" and group_of(m) not in confirmed_groups:
                band = "unchecked"  # a likely match in a group not audited yet: no rating
        elif band == "review" and verdict == "right":
            band = "checked"  # the matcher wasn't sure; a hand check was
        rating = ratings.get(vid) if vid and band in RATED_BANDS else None
        avg = rating["average"] if rating and rating.get("average") else None  # 0 means below Vivino's threshold
        count = rating["count"] if rating else None
        region = " / ".join(x for x in (p.get("originLevel1"), p.get("originLevel2")) if x)
        index[pid] = len(wines)
        wines.append({
            "id": pid, "art": art, "name": p.get("productNameBold") or "", "thin": p.get("productNameThin"),
            "prod": p.get("producerName"), "vint": p.get("vintage"), "price": p.get("price"), "vol": p.get("volume"),
            "abv": p.get("alcoholPercentage"), "sugar": p.get("sugarContentGramPer100ml"),
            "c": looks["country"](p.get("country")), "r": looks["region"](region), "ty": looks["type"](p.get("categoryLevel2")),
            "st": looks["style"](p.get("categoryLevel3")), "g": [looks["grape"](g) for g in p.get("grapes") or [] if g],
            "as": looks["assortment"](p.get("assortmentText")), "pk": looks["packaging"](p.get("packagingLevel1")),
            "org": 1 if p.get("isOrganic") else 0, "new": (p.get("productLaunchDate") or "")[:10] or None,
            "viv": int(vid) if vid else None, "band": band,
            "rat": avg, "cnt": count, "adj": adjusted(avg, count) if avg and count else None,
            "rchk": rating.get("checked_at") if rating else None, "ns": p.get("ns"),
            "img": 1 if (DATA / "app" / "img" / f"{pid}.webp").exists() else 0,  # a copy on the site (photos.py)
        })

    avail = {sid: [[index[pid], stock, shelf] for pid, stock, shelf in s["wines"] if pid in index]
             for sid, s in src["stores"].items()}
    order_range = orders.wines()
    avail["orders"] = [[index[pid], None, None] for pid in order_range if pid in index]
    lists = src["stores"]
    stores = [{"id": str(s["siteId"]), "name": store_name(s),
               "town": (s.get("city") or "").title(), "address": s.get("address"),
               "lat": (s.get("position") or {}).get("latitude"), "lng": (s.get("position") or {}).get("longitude"),
               "wines": len(avail.get(str(s["siteId"]), [])),
               "chk": _day(lists.get(str(s["siteId"]), {}).get("checked_at")),
               **_flags(lists.get(str(s["siteId"]), {}).get("status"))}
              for s in src["store_info"] if s.get("isActive", True)]
    values(wines)
    rated = [w for w in wines if w["rat"]]
    checked = sorted(w["rchk"] for w in wines if w["rchk"])
    bands = collections.Counter(w["band"] for w in wines)
    meta = {"schema": SCHEMA, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "wines": len(wines), "rated": len(rated), "bands": dict(bands), "stores": len(stores),
            "stores_with_wines": sum(1 for s in stores if s["wines"]),
            "stores_stale": sum(1 for s in stores if s.get("stale") or s.get("partial")),
            "stock_checked_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(src["fetched_at"])),
            "orders": {"wines": len(avail["orders"]), "in_no_store": sum(1 for w in wines if not w["ns"]),
                       "checked_at": _day(load_json(orders.OUT)["fetched_at"])} if order_range else None,
            "ratings_checked": [checked[0], checked[-1]] if checked else None,
            "matcher_version": next(iter(matches.values()), {}).get("matcher_version"),
            "issues": issues_url(),
            # Pairings the build knows were reported ("article:vivino id"): the phone drops its own copy of those.
            "reported": sorted({f"{r['article']}:{r['vivino_id']}" for r in state.reports().values()})}

    out = DATA / "app"
    out.mkdir(parents=True, exist_ok=True)
    (out / "avail").mkdir(exist_ok=True)
    active = {s["id"] for s in stores}
    for old in (out / "avail").glob("*.json"):
        if old.stem not in active | {"orders"}:
            old.unlink()
    files = {f"avail/{sid}.json": avail.get(sid, []) for sid in active | {"orders"}}
    files |= {"wines.json": {"schema": SCHEMA, "lookups": {k: v.values for k, v in looks.items()},
                             "wines": [compact(w) for w in wines]},
              "stores.json": stores, "meta.json": meta}  # meta last: its build time marks a complete build
    for name, data in files.items():
        tmp = out / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(out / name)  # the app never reads a half-written file
    return meta


def issues_url() -> str | None:
    """New-issue page of the repo holding state/, where the app's "Wrong match?" files reports."""
    slug = state.repo()
    return f"https://github.com/{slug}/issues/new" if slug else None


def _flags(status: str | None) -> dict:
    return {"stale": True} if status == "stale" else {"partial": True} if status == "incomplete" else {}


def _day(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None
