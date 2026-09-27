"""Build step: compact data files for the app in data/app/, from local state only (no network).

Inputs: data/assortment.json (every store's wines with stock and shelf, and the store list), state/matches.csv +
state/overrides.csv (what each wine is on Vivino) and data/ratings.json (ratings by Vivino id).

Outputs (gitignored, they hold Vivino ratings):
- wines.json: one compact row per wine; repeated strings as lookup tables; the adjusted rating precomputed
  (docs/plan.md, Ranking). A rating only for an override, or an accept whose group meets the precision bar
  (report.py) or that Adam checked right; any other accept goes out as band "unchecked", with no rating.
- avail/<storeId>.json: [wine index, stock, shelf] for every wine the store carries, so a phone downloads one
  store (tens of kB), not all of them. Stock null = in the store's assortment but no stock row yet (upcoming
  launches, order-only wines not delivered).
- stores.json: id, name, town, address, position, wine count, and the date its list was checked; "stale" when
  the last fetch didn't add up and the store kept its previous list, "partial" when there was no previous list
  and today's is known to be incomplete.
- meta.json: schema version, build time, counts, rating and stock dates.
"""

from __future__ import annotations

import collections
import json
import time

from . import state
from .assortment import OUT
from .net import DATA, load_json
from .report import group_of, groups

SCHEMA = 1
PRIOR_MEAN = 3.9  # C: in-store median rating
PRIOR_WEIGHT = 100  # m: ratings worth of pull toward C
RATED_BANDS = ("accept", "override")


def adjusted(avg: float, count: int) -> float:
    """(n·r + m·C) / (n + m): a 4.5 from 12 ratings doesn't outrank a 4.3 from 20,000."""
    return round((count * avg + PRIOR_WEIGHT * PRIOR_MEAN) / (count + PRIOR_WEIGHT), 3)


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
    ratings = load_json("ratings.json") if (DATA / "ratings.json").exists() else {}
    looks = {k: Lookup() for k in ("country", "region", "type", "style", "grape", "assortment", "packaging")}

    wines, index = [], {}
    for pid, p in src["wines"].items():
        art = str(p["productNumber"])
        m = matches.get(art, {})
        ov = overrides.get(art)
        band = "override" if ov else m.get("band", "")
        vid = (ov["vivino_id"] if ov else m.get("vivino_id", "")) or ""
        vid = "" if vid.lower() == "none" or not vid.strip().isdigit() else vid.strip()  # a typo in overrides: no id
        if band == "accept":
            verdict = labels.get((art, vid), {}).get("verdict")
            if verdict in ("wrong", "unsure"):
                band = "review"  # checked and not confirmed, and no override set yet
            elif verdict != "right" and group_of(m) not in confirmed_groups:
                band = "unchecked"  # a likely match in a group not audited yet: no rating
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
        })

    avail = {sid: [[index[pid], stock, shelf] for pid, stock, shelf in s["wines"] if pid in index]
             for sid, s in src["stores"].items()}
    lists = src["stores"]
    stores = [{"id": str(s["siteId"]), "name": store_name(s),
               "town": (s.get("city") or "").title(), "address": s.get("address"),
               "lat": (s.get("position") or {}).get("latitude"), "lng": (s.get("position") or {}).get("longitude"),
               "wines": len(avail.get(str(s["siteId"]), [])),
               "chk": _day(lists.get(str(s["siteId"]), {}).get("checked_at")),
               **_flags(lists.get(str(s["siteId"]), {}).get("status"))}
              for s in src["store_info"] if s.get("isActive", True)]
    rated = [w for w in wines if w["rat"]]
    checked = sorted(w["rchk"] for w in wines if w["rchk"])
    bands = collections.Counter(w["band"] for w in wines)
    meta = {"schema": SCHEMA, "built_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "wines": len(wines), "rated": len(rated), "bands": dict(bands), "stores": len(stores),
            "stores_with_wines": sum(1 for s in stores if s["wines"]),
            "stores_stale": sum(1 for s in stores if s.get("stale") or s.get("partial")),
            "stock_checked_at": time.strftime("%Y-%m-%d %H:%M", time.localtime(src["fetched_at"])),
            "ratings_checked": [checked[0], checked[-1]] if checked else None,
            "matcher_version": next(iter(matches.values()), {}).get("matcher_version")}

    out = DATA / "app"
    out.mkdir(parents=True, exist_ok=True)
    (out / "avail").mkdir(exist_ok=True)
    active = {s["id"] for s in stores}
    for old in (out / "avail").glob("*.json"):
        if old.stem not in active:
            old.unlink()
    files = {f"avail/{sid}.json": avail.get(sid, []) for sid in active}
    files |= {"wines.json": {"schema": SCHEMA, "lookups": {k: v.values for k, v in looks.items()}, "wines": wines},
              "stores.json": stores, "meta.json": meta}  # meta last: its build time marks a complete build
    for name, data in files.items():
        tmp = out / (name + ".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
        tmp.replace(out / name)  # the app never reads a half-written file
    return meta


def _flags(status: str | None) -> dict:
    return {"stale": True} if status == "stale" else {"partial": True} if status == "incomplete" else {}


def _day(ts: float | None) -> str | None:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts)) if ts else None
