"""A mocked Systembolaget (five stores), Algolia and api.vivino.com for the offline tests. No network.

Import this before any pipeline module: it points WINE_DATA and WINE_STATE at fresh temp dirs, speeds up the
politeness policies and swaps in the mock transport.
"""

from __future__ import annotations

import collections
import json
import os
import random
import shutil
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

DATA = Path(tempfile.mkdtemp(prefix="pipeline-data-"))
STATE = Path(tempfile.mkdtemp(prefix="pipeline-state-"))
os.environ["WINE_DATA"], os.environ["WINE_STATE"] = str(DATA), str(STATE)
for k in ("SB_API_KEY", "VIVINO_ALGOLIA_APP_ID", "VIVINO_ALGOLIA_API_KEY"):
    os.environ.pop(k, None)

import httpx  # noqa: E402

from pipeline import assortment, net  # noqa: E402

FAST = {"spacing": 0, "min_interval": 0.001, "backoff": 0.01, "max_403": 3, "cooldown": 0.01, "max_interval": 0}
net.SB = net.Policy("systembolaget", workers=4, retries=1, **FAST)
net.VIVINO = net.Policy("vivino", workers=1, retries=1, **FAST)
net.OTHER = net.Policy("other", workers=2, retries=1, **FAST)
assortment.SLICE_ABOVE = 20  # so the 102 Fast sortiment wines are fetched per type

KEY = "0123456789abcdef0123456789abcdef"
FUTURE, PAST = "2099-10-01T00:00:00", "2020-01-01T00:00:00"
FAST_A, TEMP, LOCAL, ORDER = "Fast sortiment", "Tillfälligt sortiment", "Lokalt & Småskaligt", "Ordervaror"
PRODUCERS = [("Cantina Birgi", "Italien", "it"), ("Finca Flichman", "Argentina", "ar"),
             ("Domaines Paul Mas", "Frankrike", "fr"), ("Penfolds", "Australien", "au")]
WORDS = ["Crudo", "Bordo", "Koonunga Hill", "Colombes", "Hermit Crab", "Gran Coronas"]
TYPE_IDS = {"Rött vin": 1, "Vitt vin": 2}
rng = random.Random(3)

# Each wine: the stores whose assortment lists it (_in) and the stores with a stock row for it (_rows).
W: list[dict] = []


def add(pid: str, a: str, typ: str, launch: str, members: set, rows: set) -> None:
    producer, country, cc = rng.choice(PRODUCERS)
    W.append({"productId": pid, "productNumber": str(int(pid) + 1000), "productNameBold": f"{rng.choice(WORDS)} {pid}",
              "productNameThin": None, "producerName": producer, "country": country, "vintage": "2022", "grapes": [],
              "isOrganic": False, "assortmentText": a, "categoryLevel1": "Vin", "categoryLevel2": typ,
              "productLaunchDate": launch, "price": 100 + len(W), "_cc": cc, "_in": set(members), "_rows": set(rows)})


for i in range(70):  # carried widely, stocked
    m = {"1208"} | ({"0102"} if i % 2 == 0 else set()) | ({"0615"} if i % 3 == 0 else set()) \
        | ({"0311"} if i % 5 == 0 else set())
    add(f"1{i:04d}", FAST_A, "Rött vin" if i % 2 == 0 else "Vitt vin", f"2020-{1 + i % 12:02d}-{1 + i % 28:02d}T00:00:00",
        m, m)
for i in range(32):  # upcoming launches: in the assortment, no stock row anywhere yet; 32 > one head page
    add(f"2{i:04d}", FAST_A, "Rött vin", FUTURE, {"1208"} | ({"0102"} if i < 5 else set()), set())
for i in range(16):  # T14: sold out everywhere but still listed by 0615; T15: in no store at all
    m = ({"1208"} if i < 14 else set()) | ({"0615"} if i < 5 or i == 14 else set())
    add(f"3{i:04d}", TEMP, "Vitt vin", PAST, m, m if i < 14 else set())
for i in range(6):  # L5: listed by 0102, no row
    add(f"4{i:04d}", LOCAL, "Rött vin", PAST, {"0102"}, {"0102"} if i < 5 else set())
for i in range(200):  # order-only nationwide; a few on shelves; O10 listed by 1208 but not delivered
    m = ({"1208"} if i <= 10 else set()) | ({"0615"} if 5 <= i <= 7 else set())
    add(f"5{i:04d}", ORDER, "Rött vin", PAST, m, m - ({"1208"} if i == 10 else set()))
BY_ID = {w["productId"]: w for w in W}
STORES = [
    {"siteId": "1208", "alias": "Hansa", "city": "MALMÖ", "address": "Stora Nygatan 50",
     "position": {"latitude": 55.6, "longitude": 13.0}, "isActive": True, "phone": "not kept"},
    {"siteId": "0102", "alias": "Fältöversten", "city": "STOCKHOLM", "isActive": True},
    {"siteId": "0615", "alias": "0615", "address": "Storgatan 1", "city": "UMEÅ", "isActive": True},
    {"siteId": "0311", "alias": "Kvarnen", "city": "GÖTEBORG", "isActive": True, "isBlocked": True},
    {"siteId": "0400", "alias": "Provning", "city": "STOCKHOLM", "isActive": True, "isTastingStore": True},
    {"siteId": "0999", "alias": "Closed", "city": "NOWHERE", "isActive": False},
]
knobs: dict = {"local_gap": True, "stock_fail": set(), "vivino_403": False,
               "head_fail": set(), "head_unstable": False, "facets_drop": {}, "order_extra": set(),
               "hidden_from_range": set(), "stock_404": set(), "stock_bad": set()}
KNOB_DEFAULTS = {k: (v.copy() if hasattr(v, "copy") else v) for k, v in knobs.items()}
calls: collections.Counter = collections.Counter()


def search(q: dict) -> httpx.Response:
    sid = q.get("storeId")
    if sid:
        assert q.get("isInStoreAssortmentSearch") == "true", q
    assert sid or q.get("assortmentText") != ORDER, "the order-only range is never fetched nationwide"
    if sid in knobs["head_fail"] and q.get("sortBy") == "ProductLaunchDate":
        return httpx.Response(500)
    base = [w for w in W if (sid in w["_in"] if sid else w["productId"] not in knobs["hidden_from_range"])]
    by_a = collections.Counter(w["assortmentText"] for w in base)  # disjunctive: ignores the assortment filter
    if sid in knobs["facets_drop"]:
        by_a.pop(knobs["facets_drop"][sid], None)
    sel = [w for w in base if q.get("assortmentText") in (None, w["assortmentText"])]
    types = collections.Counter(w["categoryLevel2"] for w in sel)
    sel = [w for w in sel if q.get("categoryLevel2") in (None, w["categoryLevel2"])]
    key = {"Name": "productNameBold", "ProductLaunchDate": "productLaunchDate", "Price": "price"}.get(q.get("sortBy"))
    if key:
        sel.sort(key=lambda w: w[key], reverse=q.get("sortDirection") == "Descending")
    doc = len(sel)
    if (sid == "0102" and q.get("assortmentText") == LOCAL and knobs["local_gap"]) or \
            (sid in knobs["order_extra"] and q.get("assortmentText") == ORDER):
        doc += 1  # says one more than it ever returns
    page = int(q["page"])
    ids = list(range((page - 1) * 30, min(page * 30, len(sel))))
    if not sid and q.get("categoryLevel2") == "Rött vin" and q.get("assortmentText") == FAST_A and q.get("sortBy") == "Name":
        ids = [i - 1 if i == 31 else i for i in ids]  # unstable paging: the name pass repeats one wine, skips one
    if sid and knobs["head_unstable"] and q.get("sortBy") == "ProductLaunchDate" and page == 2:
        ids = [ids[0] - 1] + ids[1:]  # ties on launch date: page 2 repeats page 1's last wine, skips its own first
    calls["store search" if sid else "range search"] += 1
    return httpx.Response(200, json={
        "metadata": {"docCount": doc, "totalPages": -(-doc // 30), "nextPage": -1 if page * 30 >= doc else page + 1},
        "products": [{k: v for k, v in sel[i].items() if not k.startswith("_")} for i in ids],
        "filterGroups": [{"filters": [
            {"name": "CategoryLevel1", "options": [{"value": "Vin", "count": len(sel), "filter": {
                "name": "CategoryLevel2", "options": [{"value": t, "count": n} for t, n in types.items()]}}]},
            {"name": "AssortmentText", "options": [{"value": a, "count": n} for a, n in by_a.items()]},
        ]}]})


def algolia(req: httpx.Request) -> httpx.Response:
    """A right hit for three wines in four, and always a decoy from another winery and country."""
    calls["algolia"] += 1
    if knobs["vivino_403"]:
        return httpx.Response(403, json={"message": "blocked"})
    query = parse_qs(json.loads(req.content)["params"])["query"][0].lower()
    hits = []
    for w in W:
        if w["productNameBold"].lower() in query:
            if int(w["productId"]) % 4:
                hits.append({"id": int(w["productId"]), "name": w["productNameBold"],
                             "type_id": TYPE_IDS[w["categoryLevel2"]],
                             "winery": {"name": w["producerName"].replace("Domaines ", "")},
                             "region": {"country": w["_cc"], "name": "Somewhere"},
                             "statistics": {"ratings_average": 3.9, "ratings_count": 400}})
            hits.append({"id": 9_000_000 + int(w["productId"]), "name": w["productNameBold"], "type_id": 1,
                         "winery": {"name": "Decoy Winery"}, "region": {"country": "us", "name": "Napa"},
                         "statistics": {"ratings_average": 4.4, "ratings_count": 50}})
    return httpx.Response(200, json={"hits": hits[:10]})


def handler(req: httpx.Request) -> httpx.Response:
    u = urlsplit(str(req.url))
    q = {k: v[0] for k, v in parse_qs(u.query).items()}
    if u.hostname == "www.systembolaget.se":
        return httpx.Response(200, text=f'<script>e={{NEXT_PUBLIC_API_KEY_APIM:"{KEY}"}}</script>')
    if u.hostname == "api-extern.systembolaget.se":
        if req.headers.get("Ocp-Apim-Subscription-Key") != KEY:
            return httpx.Response(401)
        route = u.path.split("/sb-api-ecommerce/", 1)[1]
        if route == "v2/productsearch/search":
            return search(q)
        if route == "v1/site/stores":
            return httpx.Response(200, json=STORES)
        if route.startswith("v1/site/stores/"):
            pid = route.rsplit("/", 1)[1]
            calls["stock"] += 1
            if pid in knobs["stock_fail"]:
                return httpx.Response(500)
            if pid in knobs["stock_404"]:
                return httpx.Response(404)
            if pid in knobs["stock_bad"]:
                return httpx.Response(200, json={})
            rows = [{"store": {"siteId": s}, "stockBalance": {"storeId": s, "stock": 0 if int(pid) % 7 == 0 else 6,
                                                              "shelf": f"A-{pid[-2:]}"}}
                    for s in sorted(BY_ID[pid]["_rows"])]
            return httpx.Response(200, json={"totalNumberOfStores": len(rows), "storeStocks": rows})
        return httpx.Response(404)
    if u.hostname == "raw.githubusercontent.com":
        return httpx.Response(200, text="const VIVINO_ALGOLIA_APP_ID = 'TESTAPP123'\n"
                                        "const VIVINO_ALGOLIA_SEARCH_KEY = 'fedcba9876543210fedcba9876543210'\n")
    if u.hostname.endswith("algolia.net"):
        return algolia(req)
    if u.hostname == "api.vivino.com":
        calls["vivino"] += 1
        wid = int(u.path.rsplit("/", 1)[1])
        return httpx.Response(200, json={"id": wid, "statistics": {"ratings_average": 3.8, "ratings_count": 420}})
    raise AssertionError(f"unexpected host {u.hostname}")


net.TRANSPORT = httpx.MockTransport(handler)


def reset_knobs() -> None:
    knobs.update({k: (v.copy() if hasattr(v, "copy") else v) for k, v in KNOB_DEFAULTS.items()})


def fresh(group: str = "systembolaget") -> None:
    """Drop one host group's cached responses (assortment and stock are cached for 12 h), reset counters."""
    shutil.rmtree(DATA / "cache" / "http" / group, ignore_errors=True)
    calls.clear()
    net.reset_groups()


def ids(prefix: str, rng_: range) -> set[str]:
    return {f"{prefix}{i:04d}" for i in rng_}
