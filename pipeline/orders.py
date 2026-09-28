"""Phase 7: the order-only range, the wines anyone can order to any store for pickup (Ordervaror), nationwide.

`fetch(http)`: the whole range, sliced by type (it's above the 9,990-per-search cap), in sorted passes until
unique = docCount per slice (assortment.fetch_range); a slice that stays short fails the step and keeps the
previous file. No stock calls: most of these wines are in no store, and those that are come with the stores' lists
(assortment.py). The range changes slowly, so the nightly fetches it when the last fetch is `max_age_days` old.
Writes data/orders.json.

`catalog()`: every wine the pipeline knows, for matching, photos, the queue and the build: the stores' wines
(with ns, the number of stores carrying each) and the order-only wines no store carries (ns 0).
"""

from __future__ import annotations

import json
import time

from .assortment import KEEP, ORDER_ONLY, OUT as ASSORTMENT, fetch_range
from .net import DATA, Http, load_json
from .sb import SB

OUT = "orders.json"


def fetch(http: Http, max_age_days: float = 0) -> dict:
    path = DATA / OUT
    if max_age_days and path.exists():
        prev = load_json(OUT)
        age = (time.time() - prev["fetched_at"]) / 86400
        if age < max_age_days:
            return {**prev, "skipped": f"last fetched {age:.1f} days ago"}
    t0 = time.time()
    products, slices = fetch_range(SB(http), [ORDER_ONLY])
    now = time.time()
    res = {"fetched_at": now, "seconds": round(now - t0, 1),
           "counts": {"wines": len(products), "slices": len(slices), "pages": sum(s["pages"] for s in slices),
                      "passes": sum(s["passes"] for s in slices)},
           "slices": slices,
           "wines": {pid: {k: p.get(k) for k in KEEP} for pid, p in sorted(products.items())}}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(res, ensure_ascii=False, separators=(",", ":")))
    tmp.replace(path)
    return res


def wines() -> dict[str, dict]:
    """The order-only range from the last fetch ({} before the first)."""
    return load_json(OUT)["wines"] if (DATA / OUT).exists() else {}


def catalog() -> dict[str, dict]:
    """productId -> wine: the stores' wines, then the order-only wines no store carries (ns 0)."""
    store = load_json(ASSORTMENT)["wines"]
    return {**store, **{pid: {**p, "ns": 0} for pid, p in wines().items() if pid not in store}}
