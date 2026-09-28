"""Step 1 (phase 4): every store's wines, with stock and shelf in each store.

1. Stores: site/stores, active stores only.
2. Per store, the first pages of its wine assortment, newest first (the "head"): up to the launches of the last
   RECENT_DAYS days. Its facets count the store's wines per assortment and per type: the targets its list is
   checked against. Every wine on these pages is in the store's assortment, including launches the store has no
   stock row for yet (upcoming ones, and recent ones whose rows lag).
3. The range of every assortment any store carries, except Ordervaror, sliced by type when large, in sorted
   passes until unique = docCount.
4. Order-only wines on shelves: per store that has any (says the facet), the assortment search with
   assortmentText=Ordervaror, in sorted passes until unique = docCount.
5. Plan A: site/stores/{productId} for every wine found lists each store that carries it, with stock and shelf.
6. A store's list is the wines with a stock row there, plus the head's wines, plus its order-only wines, checked
   against the facet counts per assortment and per type. A gap gets that part of the store fetched directly (the
   phase 2 method: the one assortment and type that are off, else the assortments, else the whole store), within
   MAX_CRAWL_PAGES, the stores whose last good list is oldest first. A
   store that still doesn't add up keeps its previous list and date ("stale"), or, with no previous list, keeps
   today's with the status "incomplete"; either way the step reports it.
7. Spot checks: SPOT_CHECKS stores that added up are fetched in full and compared, to measure how often a list
   that adds up is still wrong (a wrong list is replaced by the fetched one).

A range slice that stays short, or a wine whose stock can't be fetched, fails the whole step and the previous
data/assortment.json stays: those gaps would reach many stores at once.

Writes data/assortment.json.
"""

from __future__ import annotations

import collections
import datetime
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Iterable

from .net import DATA, Http, load_json
from .sb import SB

OUT = "assortment.json"
WINE = {"categoryLevel1": "Vin"}
ORDER_ONLY = "Ordervaror"
ASSORTMENT_MAX_AGE = 12 * 3600  # a rerun within 12 h resumes from the cache; stock changes daily
STOCK_MAX_AGE = 12 * 3600
STORES_MAX_AGE = 86400
RECENT_DAYS = 14  # the head reads launches this recent, since a new wine's stock rows can lag its launch
SLICE_ABOVE = 600  # range assortments larger than this are fetched per type: fewer sort ties, fewer extra passes
MAX_HEAD_PAGES = 10
MAX_CRAWL_PAGES = 600  # first-pass pages of direct fetches per run (~5 min); stores beyond this go stale
SPOT_CHECKS = 2
KEEP = ("productId", "productNumber", "productNameBold", "productNameThin", "producerName", "supplierName",
        "vintage", "country", "originLevel1", "originLevel2", "categoryLevel2", "categoryLevel3", "assortmentText",
        "grapes", "volume", "price", "alcoholPercentage", "sugarContentGramPer100ml", "packagingLevel1", "isOrganic",
        "isCompletelyOutOfStock", "isTemporaryOutOfStock", "isDiscontinued", "productLaunchDate")
STORE_KEEP = ("siteId", "alias", "address", "city", "position", "isActive", "isBlocked", "isTastingStore")


class Incomplete(RuntimeError):
    """A fetch that didn't return everything the API says exists."""


def stock_rows(data: object) -> dict[str, dict] | None:
    """site/stores/{productId} -> {storeId: {"stock": n, "shelf": s}}, or None for a body of the wrong shape."""
    if not isinstance(data, dict) or not isinstance(data.get("storeStocks"), list):
        return None
    out: dict[str, dict] = {}
    for row in data["storeStocks"]:
        bal = row.get("stockBalance") or {}
        sid = bal.get("storeId") or (row.get("store") or {}).get("siteId")
        if sid is not None:
            out[str(sid)] = {"stock": bal.get("stock"), "shelf": bal.get("shelf")}
    total = data.get("totalNumberOfStores")
    return out if total is None or total == len(out) else None  # the spike: always equal


def facets(page: dict) -> dict[str, dict[str, int]]:
    """Facet counts of one search page, nested facets included: {filter name: {value: count}}.

    The AssortmentText facet is disjunctive: a search filtered to Ordervaror still counts every assortment."""
    out: dict[str, dict[str, int]] = {}

    def walk(f: dict) -> None:
        opts = f.get("options") or []
        if opts and f.get("name"):
            out[f["name"]] = {o["value"]: o.get("count") or 0 for o in opts if o.get("value") is not None}
        for o in opts:
            if isinstance(o.get("filter"), dict):
                walk(o["filter"])

    for g in page.get("filterGroups") or []:
        for f in g.get("filters") or []:
            walk(f)
    return out


def _pid(p: dict) -> str:
    return str(p["productId"])


def _launch(p: dict) -> str:
    return (p.get("productLaunchDate") or "")[:10]


def _pmap(fn: Callable, items: Iterable) -> list:
    """Run fn over items with 4 workers; the Http group keeps the shared pace."""
    with ThreadPoolExecutor(max_workers=4) as ex:
        return list(ex.map(fn, items))


def _in_store(sid: str, **extra: str) -> dict:
    return {**WINE, "storeId": sid, "isInStoreAssortmentSearch": "true", **extra}


def head(sb: SB, sid: str, since: str) -> dict:
    """The store's facet counts, and its wines launched on `since` or later (future launches included)."""
    params = _in_store(sid, sortBy="ProductLaunchDate", sortDirection="Descending")
    r, d = sb.search(params, 1, max_age=ASSORTMENT_MAX_AGE)
    if not r.ok:
        return {"status": r.status}
    meta = d.get("metadata") or {}
    products = list(d.get("products") or [])
    last = min(meta.get("totalPages") or 1, MAX_HEAD_PAGES)
    page = 1
    while products and _launch(products[-1]) >= since and page < last:
        page += 1
        r, more = sb.search(params, page, max_age=ASSORTMENT_MAX_AGE)
        if not r.ok:
            return {"status": r.status}
        products += more.get("products") or []
    f = facets(d)
    return {"status": 200, "doc_count": meta.get("docCount"), "by_assortment": f.get("AssortmentText") or {},
            "by_type": f.get("CategoryLevel2") or {}, "pages": page, "newest": products}


def fetch_range(sb: SB, assortments: list[str]) -> tuple[dict[str, dict], list[dict]]:
    """Every wine in these assortments, nationwide. Large ones per type; each slice must be complete."""
    products: dict[str, dict] = {}
    slices = []
    for a in assortments:
        base = {**WINE, "assortmentText": a}
        r, first = sb.search(base, 1, max_age=ASSORTMENT_MAX_AGE)
        doc = (first.get("metadata") or {}).get("docCount")
        if not r.ok or not isinstance(doc, int):
            raise Incomplete(f"range {a}: HTTP {r.status}, docCount {doc}")
        types = facets(first).get("CategoryLevel2") or {}
        parts = [base]
        if doc > SLICE_ABOVE and types and sum(types.values()) == doc:
            parts = [{**base, "categoryLevel2": t} for t in types]
        got = 0
        for params in parts:
            out = sb.search_all(params, max_age=ASSORTMENT_MAX_AGE)
            slices.append({"assortment": a, "type": params.get("categoryLevel2"), "doc_count": out["doc_count"],
                           "unique": out["unique"], "pages": out["pages"], "passes": len(out["passes"])})
            if not out["complete"]:
                raise Incomplete(f"range {a} / {params.get('categoryLevel2') or 'all types'}: {out['unique']} unique "
                                 f"of docCount {out['doc_count']}, failed pages {out['failed_pages']}")
            got += out["unique"]
            for p in out["products"]:
                products[_pid(p)] = p
        if got != doc:
            raise Incomplete(f"range {a}: slices add up to {got}, docCount {doc}")
    return products, slices


def fetch_stock(sb: SB, pids: list[str]) -> tuple[dict[str, dict], list[str]]:
    """Plan A: {productId: {storeId: {"stock", "shelf"}}}, and the wines Systembolaget says don't exist (404 twice:
    carried nowhere). Failures get one retry without the cache; a wine still failing fails the step."""
    def one(pid: str, cached: bool) -> tuple[str, int, dict | None]:
        r = sb.get(f"site/stores/{pid}", max_age=STOCK_MAX_AGE, cache=cached)
        return pid, r.status, stock_rows(r.data()) if r.ok else None

    rows: dict[str, dict] = {}
    todo, gone, failed = pids, [], []
    for cached in (True, False):
        failed = []
        for pid, status, rr in _pmap(lambda pid: one(pid, cached), todo):
            if rr is not None:
                rows[pid] = rr
            elif status == 404 and not cached:
                gone.append(pid)
                rows[pid] = {}
            else:
                failed.append((pid, status if rr is None and status != 200 else "bad body"))
        todo = [pid for pid, _ in failed]
        if not todo:
            return rows, gone
    raise Incomplete(f"stock failed for {len(failed)} wines, e.g. {failed[:5]}")


def listing(h: dict, orders: dict | None, here: dict[str, list], products: dict[str, dict],
            crawls: dict[tuple, dict], today: str) -> tuple[dict[str, list], dict]:
    """One store's wines {productId: [stock, shelf]} (stock None = no row yet: not stocked), and its check.

    Targets: the head's facet counts per assortment and per type, or, for a part fetched directly (the order-only
    search, a crawl of a whole assortment), that search's own count: it is the newer of the two. A wine in a
    fetched part counts under that part's assortment, even if an earlier search said otherwise (it moved while the
    run went on). Crawl keys are (assortment, type), None meaning any.

    Stock rows for wines not launched yet that the store's own pages don't list are deliveries ahead of the launch,
    before the wine joins the store's assortment: listed (they arrive), never counted, never dropped by a crawl."""
    gaps: dict[str, list] = {}
    moved: dict[str, str] = {}

    def assortment(pid: str) -> str | None:
        return moved.get(pid) or (products.get(pid) or {}).get("assortmentText")

    def kind(pid: str) -> str | None:
        return (products.get(pid) or {}).get("categoryLevel2")

    on_pages = {_pid(p) for p in h["newest"]}
    early = {pid for pid in here if _launch(products.get(pid) or {}) > today and pid not in on_pages
             and assortment(pid) != ORDER_ONLY}
    if (None, None) in crawls:  # the whole store was fetched directly: that is the list
        out = crawls[(None, None)]
        listed = {pid: here.get(pid, [None, None]) for pid in (_pid(p) for p in out["products"])}
        listed |= {pid: here[pid] for pid in early}
        if not out["complete"]:
            gaps["all"] = [out["doc_count"], out["unique"]]
        return listed, _check(gaps, listed, here, early)
    want = dict(h["by_assortment"])
    if sum(want.values()) != h["doc_count"]:
        gaps["facets"] = [h["doc_count"], sum(want.values())]
    listed = {pid: v for pid, v in here.items() if assortment(pid) != ORDER_ONLY}
    for pid in on_pages:  # on the store's own pages: in its assortment, with a row or not
        if assortment(pid) != ORDER_ONLY:
            listed.setdefault(pid, here.get(pid, [None, None]))
    parts = list(crawls.items()) + ([((ORDER_ONLY, None), orders)] if orders is not None else [])
    for (a, t), out in parts:  # a part fetched directly replaces what rows and head said about it
        members = {_pid(p) for p in out["products"]}
        moved |= dict.fromkeys(members, a)
        listed = {pid: v for pid, v in listed.items()
                  if pid in members or pid in early or assortment(pid) != a or (t is not None and kind(pid) != t)}
        for pid in members:
            listed.setdefault(pid, here.get(pid, [None, None]))
        if t is None:
            want[a] = out["doc_count"]
        if not out["complete"]:
            gaps[a if t is None else f"{a} / {t}"] = [out["doc_count"], out["unique"]]
    counted = [pid for pid in listed if pid not in early]
    have = collections.Counter(assortment(pid) for pid in counted)
    for a in set(want) | set(have):
        if want.get(a, 0) != have.get(a, 0):
            gaps.setdefault(a if a is not None else "none", [want.get(a, 0), have.get(a, 0)])
    if not crawls:  # types: the head's count is the target unless part of the store was re-fetched
        types = collections.Counter(kind(pid) for pid in counted)
        for t in set(h["by_type"]) | set(types):
            if h["by_type"].get(t, 0) != types.get(t, 0):
                gaps.setdefault(f"type {t}", [h["by_type"].get(t, 0), types.get(t, 0)])
    return listed, _check(gaps, listed, here, early)


def _check(gaps: dict, listed: dict, here: dict, early: set) -> dict:
    return {"gaps": gaps, "no_row": sum(1 for v in listed.values() if v[0] is None), "early": len(early),
            "rows_outside": sum(1 for pid in here if pid not in listed)}


def crawl_parts(gaps: dict) -> list[tuple]:
    """What to fetch directly for these gaps, as (assortment, type) with None for any: the one assortment and type
    that are off by the same amount, else the assortments that are off, else the whole store."""
    if not gaps:
        return []
    a_gaps = {k: v for k, v in gaps.items() if not k.startswith("type ")}
    t_gaps = {k.removeprefix("type "): v for k, v in gaps.items() if k.startswith("type ")}
    if not a_gaps or a_gaps.keys() & {ORDER_ONLY, "none", "facets", "all"}:
        return [(None, None)]
    if len(a_gaps) == 1 and len(t_gaps) == 1:
        (a, (wa, ha)), = a_gaps.items()
        (t, (wt, ht)), = t_gaps.items()
        if ha - wa == ht - wt:
            return [(a, t)]
    return [(a, None) for a in a_gaps]


def crawl_pages(h: dict, part: tuple) -> int:
    """First-pass pages a crawl of this part costs, from the head's facet counts."""
    a, t = part
    n = h["doc_count"] if a is None else h["by_assortment"].get(a, 0)
    if t is not None:
        n = min(n, h["by_type"].get(t, n))
    return max(1, -(-(n or 0) // 30))


def fetch(http: Http) -> dict:
    sb = SB(http)
    t0 = time.time()
    today = datetime.date.today()
    since = (today - datetime.timedelta(days=RECENT_DAYS)).isoformat()
    timing: dict[str, float] = {}

    def lap(name: str) -> None:
        timing[name] = max(0.0, round(time.time() - t0 - sum(timing.values()), 1))
        print(f"fetch: {name} done, {timing[name]} s", flush=True)

    r = sb.get("site/stores", max_age=STORES_MAX_AGE)
    info = [{k: s.get(k) for k in STORE_KEEP} for s in r.data() or [] if s.get("isActive", True)]
    stores = [str(s["siteId"]) for s in info]
    if not stores:
        raise Incomplete(f"site/stores: HTTP {r.status}, no active stores")

    heads = dict(zip(stores, _pmap(lambda sid: head(sb, sid, since), stores)))
    lap("heads")
    in_stores = sorted({a for h in heads.values() for a, n in (h.get("by_assortment") or {}).items() if n}
                       - {ORDER_ONLY})
    products, slices = fetch_range(sb, in_stores)
    lap("range")
    with_orders = [s for s, h in heads.items() if (h.get("by_assortment") or {}).get(ORDER_ONLY)]
    orders = dict(zip(with_orders, _pmap(
        lambda sid: sb.search_all(_in_store(sid, assortmentText=ORDER_ONLY), max_age=ASSORTMENT_MAX_AGE),
        with_orders)))
    for p in [*(p for o in orders.values() for p in o["products"]),
              *(p for h in heads.values() for p in h.get("newest") or [])]:
        products.setdefault(_pid(p), p)
    lap("order_only")
    rows, gone = fetch_stock(sb, sorted(products))
    lap("stock")

    def by_store() -> dict[str, dict[str, list]]:
        out: dict[str, dict[str, list]] = collections.defaultdict(dict)
        for pid, rr in rows.items():
            for sid, row in rr.items():
                out[sid][pid] = [row["stock"], row["shelf"]]
        return out

    def crawl(sid: str, part: tuple) -> dict:
        a, t = part
        extra = {k: v for k, v in (("assortmentText", a), ("categoryLevel2", t)) if v}
        return sb.search_all(_in_store(sid, **extra), max_age=ASSORTMENT_MAX_AGE)

    def add_new(found: Iterable[dict]) -> None:
        """Wines seen only in a direct fetch: their stock rows too, which may touch other stores."""
        nonlocal inverted
        new = {_pid(p): p for out in found for p in out["products"] if _pid(p) not in products}
        if new:
            products.update(new)
            more, lost = fetch_stock(sb, sorted(new))
            rows.update(more)
            gone.extend(lost)
            inverted = by_store()

    # Check every store; fetch the parts that don't add up directly, within MAX_CRAWL_PAGES: the stores whose
    # last good list is oldest first, so a store left stale tonight comes first tomorrow. Then check again.
    prev = load_json(OUT) if (DATA / OUT).exists() else {"stores": {}, "wines": {}}
    inverted = by_store()
    gapped: dict[str, list[tuple]] = {}
    for sid, h in heads.items():
        if h["status"] == 200:
            _, check = listing(h, orders.get(sid), inverted.get(sid, {}), products, {}, today.isoformat())
            if check["gaps"]:
                gapped[sid] = crawl_parts(check["gaps"])
    budget, chosen = MAX_CRAWL_PAGES, []
    def last_good(sid: str) -> float:
        """When the store last had a list that added up: an incomplete list counts as never."""
        entry = prev["stores"].get(sid, {})
        return entry.get("checked_at") or 0 if entry.get("status") in ("ok", "crawled", "stale") else 0

    for sid in sorted(gapped, key=lambda s: (last_good(s), sum(crawl_pages(heads[s], p) for p in gapped[s]))):
        cost = sum(crawl_pages(heads[sid], p) for p in gapped[sid])
        if cost <= budget:
            chosen.append(sid)
            budget -= cost
    capped = [sid for sid in gapped if sid not in chosen]
    crawls: dict[str, dict[tuple, dict]] = dict(zip(chosen, _pmap(
        lambda sid: {p: crawl(sid, p) for p in gapped[sid]}, chosen)))
    add_new(out for c in crawls.values() for out in c.values())
    if gapped:
        lap("crawls")

    def assemble(sid: str) -> tuple[dict[str, list], dict]:
        h = heads[sid]
        if h["status"] != 200:
            return {}, {"gaps": {"head": [h["status"], None]}}
        return listing(h, orders.get(sid), inverted.get(sid, {}), products, crawls.get(sid, {}), today.isoformat())

    # Spot checks: fetch a few stores that added up in full, and compare.
    rnd = random.Random(today.isoformat())
    ok = sorted(sid for sid in stores if sid not in gapped and heads[sid]["status"] == 200 and heads[sid]["doc_count"])
    spots = rnd.sample(ok, min(SPOT_CHECKS, len(ok)))
    spot_crawls = dict(zip(spots, _pmap(lambda sid: crawl(sid, (None, None)), spots)))
    add_new(spot_crawls.values())
    spot = []
    for sid, out in spot_crawls.items():
        listed, _ = assemble(sid)
        full = {_pid(p) for p in out["products"]}
        mine = {pid for pid in listed if pid in full or _launch(products.get(pid) or {}) <= today.isoformat()}
        spot.append({"store": sid, "complete": out["complete"], "listed": len(mine), "fetched": len(full),
                     "missing": len(full - mine), "extra": len(mine - full)})  # pre-launch deliveries aside
        if out["complete"] and full != mine:
            crawls[sid] = {(None, None): out}  # the fetched list wins
    if spots:
        lap("spot_checks")

    now = time.time()
    out_stores: dict[str, dict] = {}
    for sid in stores:
        h = heads[sid]
        listed, check = assemble(sid)
        entry = {"doc_count": h.get("doc_count"), "by_assortment": h.get("by_assortment"), **check}
        if sid in capped:
            entry["capped"] = True
        if not check["gaps"]:
            entry |= {"status": "crawled" if sid in crawls else "ok", "checked_at": now,
                      "wines": [[pid, *v] for pid, v in sorted(listed.items())]}
        elif prev["stores"].get(sid, {}).get("status") in ("ok", "crawled", "stale"):
            old = prev["stores"][sid]  # the last list that added up, with its date
            entry |= {"status": "stale", "checked_at": old["checked_at"], "wines": old["wines"]}
        else:
            entry |= {"status": "incomplete", "checked_at": now,
                      "wines": [[pid, *v] for pid, v in sorted(listed.items())]}
        out_stores[sid] = entry

    carried = collections.Counter(row[0] for s in out_stores.values() for row in s["wines"])
    wines = {}
    for pid in sorted(carried):
        p = products.get(pid) or prev["wines"].get(pid)  # a stale store's wine may be gone from today's searches
        if p:
            wines[pid] = {**{k: p.get(k) for k in KEEP}, "ns": carried[pid]}
    status = collections.Counter(s["status"] for s in out_stores.values())
    res = {
        "fetched_at": now, "today": today.isoformat(), "seconds": round(now - t0, 1), "timing": timing,
        "counts": {"stores": len(stores), "by_status": dict(status), "wines": len(wines),
                   "range": sum(s["unique"] for s in slices), "order_only_stores": len(with_orders),
                   "order_only_wines": len({_pid(p) for o in orders.values() for p in o["products"]}),
                   "head_pages": sum(h.get("pages") or 0 for h in heads.values()),
                   "no_row": sum(s.get("no_row") or 0 for s in out_stores.values()),
                   "early_rows": sum(s.get("early") or 0 for s in out_stores.values()),
                   "in_no_store": sum(1 for pid in products if pid not in carried), "stock_404": len(gone),
                   "store_wine_pairs": sum(carried.values()), "gapped_stores": len(gapped),
                   "capped_stores": len(capped), "crawled_parts": sum(len(c) for c in crawls.values()),
                   "rows_outside": sum(s.get("rows_outside") or 0 for s in out_stores.values())},
        "spot_checks": spot, "assortments": in_stores, "slices": slices, "store_info": info,
        "stores": out_stores, "wines": wines,
    }
    path = DATA / OUT
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)  # the build never reads a half-written file
    return res
