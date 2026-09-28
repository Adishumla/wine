"""The order-only range (phase 7): fetched nationwide in its own step, matched as its own cohort, in the build as
"Order only". Against the shared mock; no network.

Run: python -m pipeline.tests.test_orders
"""

from __future__ import annotations

import json

from pipeline.tests.mock import DATA, STATE, W, calls, fresh, knobs  # noqa: I001 (sets up first)

from pipeline import orders, run, state  # noqa: E402
from pipeline.build import expand  # noqa: E402
from pipeline.report import group_of, groups  # noqa: E402

ORDER_RANGE = {w["productId"] for w in W if w["assortmentText"] == "Ordervaror"}


def store_wines() -> dict:
    return json.loads((DATA / "assortment.json").read_text())["wines"]


def test_fetch_never_asks_for_the_range_and_orders_does() -> None:
    fresh()
    knobs["local_gap"] = False
    (STATE / "matches.csv").unlink(missing_ok=True)
    calls.clear()
    assert run.main(["fetch"]) == 0
    assert calls["order range"] == 0  # the stores' fetch never pages through the whole range
    assert orders.catalog() == store_wines()  # before the first orders fetch
    assert run.main(["orders"]) == 0
    got = json.loads((DATA / "orders.json").read_text())
    assert set(got["wines"]) == ORDER_RANGE and got["counts"]["wines"] == len(ORDER_RANGE)
    assert calls["order range"] > 0
    calls.clear()
    assert run.main(["orders", "--max-age", "6"]) == 0 and calls["order range"] == 0  # fresh: skipped
    cat, store = orders.catalog(), store_wines()
    assert set(cat) == set(store) | ORDER_RANGE
    assert all(cat[pid]["ns"] == 0 for pid in ORDER_RANGE - set(store))
    assert all(cat[pid]["ns"] == store[pid]["ns"] for pid in store)  # shelf wines keep their store count


def test_order_only_wines_match_last_as_their_own_cohort() -> None:
    store = store_wines()
    assert run.main(["match", "--limit", str(len(store))]) == 0
    rows = state.matches()
    assert {r["product_id"] for r in rows.values()} == set(store)  # store wines first
    assert run.main(["match"]) == 0
    rows = state.matches()
    only = {r["product_id"]: r for r in rows.values() if r["product_id"] in ORDER_RANGE - set(store)}
    assert len(only) == len(ORDER_RANGE - set(store)) and {r["cohort"] for r in only.values()} == {"p7"}
    assert {group_of(r) for r in only.values()} == {"Ordervaror (order-only)"}
    shelf = [r for r in rows.values() if r["product_id"] in ORDER_RANGE & set(store)]
    assert shelf and {r["cohort"] for r in shelf} == {"p4"}  # on a shelf when first matched: a store wine
    g = groups()["Ordervaror (order-only)"]
    assert g["accept"] and not g["meets_target"]
    last = json.loads((DATA / "match.json").read_text())
    assert last["order_only"] == len(ORDER_RANGE - set(store))
    assert "Ordervaror (order-only)" in last["new_by_assortment"]


def test_build_has_the_order_only_list() -> None:
    (DATA / "ratings.json").write_text(json.dumps({r["vivino_id"]: {"average": 4.0, "count": 300, "checked_at": "2026-09-28"}
                                                   for r in state.matches().values() if r["vivino_id"]}))
    assert run.main(["build"]) == 0
    app = DATA / "app"
    wines = [expand(w) for w in json.loads((app / "wines.json").read_text())["wines"]]
    by_id = {w["id"]: (i, w) for i, w in enumerate(wines)}
    lst = json.loads((app / "avail" / "orders.json").read_text())
    assert sorted(wines[i]["id"] for i, _, _ in lst) == sorted(ORDER_RANGE)
    assert all(stock is None and shelf is None for _, stock, shelf in lst)
    meta = json.loads((app / "meta.json").read_text())
    store = store_wines()
    assert meta["orders"]["wines"] == len(ORDER_RANGE)
    assert meta["orders"]["in_no_store"] == len(ORDER_RANGE - set(store))
    only = [by_id[pid][1] for pid in ORDER_RANGE - set(store)]
    # The order-only group isn't audited yet: its accepts carry no rating.
    assert {w["band"] for w in only} <= {"unchecked", "review", "reject"} and not any(w["rat"] for w in only)
    assert all(w["ns"] == 0 for w in only)
    assert not any("orders" == s["id"] for s in json.loads((app / "stores.json").read_text()))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
