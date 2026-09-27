"""Every store's wines (phase 4 step 1) and the build step, against a mocked Systembolaget. No network.

Run: python -m pipeline.tests.test_assortment
"""

from __future__ import annotations

import csv
import json

from pipeline.tests.mock import BY_ID, DATA, FAST_A, LOCAL, STATE, TEMP, calls, fresh, ids, knobs, reset_knobs  # noqa: I001

import httpx  # noqa: E402

from pipeline import assortment, net, run, state  # noqa: E402


def out() -> dict:
    return json.loads((DATA / assortment.OUT).read_text())


def test_every_store() -> None:
    fresh()
    assert run.main(["fetch"]) == 1  # 0102's local wines never add up
    a = out()
    s = a["stores"]
    assert {k: v["status"] for k, v in s.items()} == {
        "1208": "ok", "0102": "incomplete", "0615": "ok", "0311": "ok", "0400": "ok"}  # 0999 is closed
    listed = {sid: {row[0]: row[1:] for row in v["wines"]} for sid, v in s.items()}
    # Hansa: stocked wines, all 32 upcoming launches (two head pages), and its order-only wines, O10 undelivered.
    assert set(listed["1208"]) == ids("1", range(70)) | ids("2", range(32)) | ids("3", range(14)) | ids("5", range(11))
    assert {p for p, (stock, _) in listed["1208"].items() if stock is None} == ids("2", range(32)) | {"50010"}
    assert listed["1208"]["10003"] == [0, "A-03"] and listed["1208"]["10001"] == [6, "A-01"]  # 10003 % 7 == 0
    assert s["1208"]["no_row"] == 33 and not s["1208"]["gaps"]
    # 0615: T14 has no row but is on the store's own pages, so it's listed, not stocked.
    assert "30014" in listed["0615"] and listed["0615"]["30014"] == [None, None]
    assert set(listed["0615"]) >= ids("5", range(5, 8)) and not s["0615"]["gaps"]
    assert s["0102"]["gaps"] == {f"{LOCAL} / Rött vin": [7, 6]}  # fetched narrowly: local red wines only
    assert listed["0400"] == {} and listed["0311"] == {f"1{i:04d}": [0 if (10000 + i) % 7 == 0 else 6, f"A-{i:02d}"]
                                                        for i in range(0, 70, 5)}
    assert "30015" not in a["wines"] and a["counts"]["in_no_store"] == 1
    assert a["wines"]["10000"]["ns"] == 4 and a["wines"]["10000"]["assortmentText"] == FAST_A
    assert a["assortments"] == [FAST_A, LOCAL, TEMP]
    by_slice = {(x["assortment"], x["type"]): x for x in a["slices"]}
    assert by_slice[(FAST_A, "Rött vin")]["passes"] == 2 and by_slice[(TEMP, None)]["passes"] == 1
    assert calls["stock"] == 102 + 16 + 6 + 11  # Plan A: every wine found, once; the other order-only wines never
    assert [x["siteId"] for x in a["store_info"]] == ["1208", "0102", "0615", "0311", "0400"]
    assert "phone" not in a["store_info"][0]
    fresh()
    run.PARTIAL_OK, old = 0.5, run.PARTIAL_OK  # 1 store of 5 off: a partial success (exit 2), not a failure
    try:
        assert run.main(["fetch"]) == 2
    finally:
        run.PARTIAL_OK = old


def test_stale_store_keeps_previous_list() -> None:
    fresh()
    knobs["local_gap"] = False
    assert run.main(["fetch"]) == 0
    first = out()["stores"]["0102"]
    assert first["status"] == "crawled" and ["40005", None, None] in first["wines"]
    fresh()
    knobs["local_gap"] = True
    assert run.main(["fetch"]) == 1
    again = out()["stores"]["0102"]
    assert again["status"] == "stale" and again["wines"] == first["wines"] and again["checked_at"] == first["checked_at"]
    assert out()["stores"]["1208"]["checked_at"] > first["checked_at"]


def test_failures_keep_previous_file() -> None:
    before = (DATA / assortment.OUT).read_text()
    fresh()
    knobs["stock_fail"] = {"10001"}  # one wine's stock can't be fetched (after retries): the step fails
    try:
        assert run.main(["fetch"]) == 1
    finally:
        knobs["stock_fail"] = set()
    assert (DATA / assortment.OUT).read_text() == before
    # More to fetch directly than MAX_CRAWL_PAGES allows: those stores go stale instead of failing every store.
    fresh()
    knobs["local_gap"] = False
    assortment.MAX_CRAWL_PAGES, old = 0, assortment.MAX_CRAWL_PAGES
    try:
        assert run.main(["fetch"]) == 1
    finally:
        assortment.MAX_CRAWL_PAGES = old
        reset_knobs()
    a = out()
    assert a["stores"]["0102"]["status"] == "stale" and a["stores"]["0102"].get("capped")
    assert a["stores"]["1208"]["status"] == "ok" and a["counts"]["capped_stores"] == 1

def listed(sid: str) -> dict[str, list]:
    return {row[0]: row[1:] for row in out()["stores"][sid]["wines"]}


def run_with(**kw) -> tuple[int, dict]:
    """One fetch with these knobs set (fresh responses), then back to the defaults."""
    fresh()
    knobs.update({"local_gap": False, **kw})
    try:
        return run.main(["fetch"]), out()
    finally:
        reset_knobs()


def test_head_ties_and_cancelling_errors() -> None:
    # Ties on launch date page unstably: U30 falls between the head's pages. It has no row, so 1208's Fast count
    # is one short and that part is fetched directly.
    code, a = run_with(head_unstable=True)
    assert code == 0 and a["stores"]["1208"]["status"] == "crawled" and listed("1208")["20030"] == [None, None]
    # X has a row in 1208 but isn't in its assortment; Y is in it without a row. The assortment counts cancel out,
    # the type counts don't: the whole store is fetched and its own list wins.
    x, y = BY_ID["10003"], BY_ID["10004"]  # Vitt vin, Rött vin
    x["_in"].discard("1208"), y["_rows"].discard("1208")
    try:
        code, a = run_with()
    finally:
        x["_in"].add("1208"), y["_rows"].add("1208")
    assert code == 0 and a["stores"]["1208"]["status"] == "crawled", a["stores"]["1208"]
    assert "10003" not in listed("1208") and listed("1208")["10004"] == [None, None]


def test_facets_order_only_and_failed_heads() -> None:
    code, a = run_with(facets_drop={"0615": TEMP})  # facets don't add up to docCount: fetch the whole store
    assert code == 0 and a["stores"]["0615"]["status"] == "crawled" and "30014" in listed("0615")
    code, a = run_with(order_extra={"1208"})  # the order-only search stays short: the whole store is fetched
    assert code == 0 and a["stores"]["1208"]["status"] == "crawled" and listed("1208")["50010"] == [None, None]
    before = listed("0311")
    code, a = run_with(head_fail={"0311"})
    assert code == 1 and a["stores"]["0311"]["status"] == "stale" and listed("0311") == before


def test_wines_seen_only_in_a_direct_fetch() -> None:
    # The range search never shows T5 (only 1208 carries it, too old for 1208's first pages): 1208's Tillfälligt
    # count is one short, that part is fetched directly, and T5 turns up with its stock.
    code, a = run_with(hidden_from_range={"30005"})
    assert code == 0 and a["stores"]["1208"]["status"] == "crawled" and listed("1208")["30005"] == [6, "A-05"]
    assert a["wines"]["30005"]["ns"] == 1 and calls["stock"] == 135


def test_stale_list_keeps_its_wine_records() -> None:
    code, _ = run_with()
    assert code == 0 and "40000" in listed("0102")
    l0 = BY_ID["40000"]
    saved = (set(l0["_in"]), set(l0["_rows"]))
    l0["_in"].clear(), l0["_rows"].clear()  # L0 disappears; 0102's local wines don't add up tonight
    try:
        code, a = run_with(local_gap=True)
    finally:
        l0["_in"].update(saved[0]), l0["_rows"].update(saved[1])
    assert code == 1 and a["stores"]["0102"]["status"] == "stale" and "40000" in listed("0102")
    assert a["wines"]["40000"]["productNumber"] == "41000"  # from the previous file


def test_stock_404_and_bad_bodies() -> None:
    code, a = run_with(stock_404={"50199"})
    assert code == 0 and a["counts"]["stock_404"] == 0  # 50199 is in no search, so never asked
    code, a = run_with(stock_404={"10001"})  # listed but "not found": carried nowhere, the stores' checks catch it
    assert a["counts"]["stock_404"] == 1 and "10001" in listed("1208") and listed("1208")["10001"] == [None, None]
    before = (DATA / assortment.OUT).read_text()
    code, _ = run_with(stock_bad={"10001"})  # a 200 of the wrong shape is a failure, not "no rows"
    assert code == 1 and (DATA / assortment.OUT).read_text() == before


def test_early_rows_and_narrow_fetches() -> None:
    # U0 launches later; 0311 has bottles already (a row) but doesn't list it yet: listed, arriving, not counted.
    u0 = BY_ID["20000"]
    u0["_rows"].add("0311")
    try:
        code, a = run_with()
    finally:
        u0["_rows"].discard("0311")
    assert code == 0 and a["stores"]["0311"]["status"] == "ok" and a["stores"]["0311"]["early"] == 1
    assert listed("0311")["20000"] == [0 if 20000 % 7 == 0 else 6, "A-00"]
    # 10005 (Fast, white) is in 1208's assortment without a row there: Fast and white are one short, so only
    # 1208's white Fast wines are fetched directly, not the whole assortment.
    y = BY_ID["10005"]
    y["_rows"].discard("1208")
    try:
        code, a = run_with()
    finally:
        y["_rows"].add("1208")
    assert code == 0 and a["stores"]["1208"]["status"] == "crawled" and listed("1208")["10005"] == [None, None]
    assert calls[f"store 1208 {FAST_A} Vitt vin"] > 0 and calls[f"store 1208 {FAST_A} None"] == 0


def test_build_offline() -> None:
    def no_network(req: httpx.Request) -> httpx.Response:
        raise AssertionError(f"build touched the network: {req.url}")

    code, _ = run_with(local_gap=True)  # fresh lists; 0102 stale (its last good list kept)
    assert code == 1

    state.write_matches([
        {"article": "11000", "product_id": "10000", "vivino_id": "111", "band": "accept", "matched_at": "2026-09-27"},
        {"article": "11002", "product_id": "10002", "vivino_id": "222", "band": "review", "matched_at": "2026-09-27"}])
    state.add_labels([{"article": "11000", "vivino_id": "111", "verdict": "right", "note": ""}])  # else unchecked
    (DATA / "ratings.json").write_text(json.dumps({
        "111": {"average": 4.1, "count": 500, "checked_at": "2026-09-27"},
        "222": {"average": 4.5, "count": 90, "checked_at": "2026-09-27"}}))
    net.TRANSPORT, transport = httpx.MockTransport(no_network), net.TRANSPORT
    try:
        assert run.main(["build"]) == 0
    finally:
        net.TRANSPORT = transport
    app = DATA / "app"
    w = json.loads((app / "wines.json").read_text())
    rows = {r["id"]: r for r in w["wines"]}
    assert len(rows) == len(out()["wines"]) and "30015" not in rows
    assert rows["10000"]["rat"] == 4.1 and rows["10000"]["adj"] == round((500 * 4.1 + 100 * 3.9) / 600, 3)
    assert rows["10002"]["rat"] is None and rows["10002"]["band"] == "review"  # not confirmed: no rating
    assert rows["10004"]["band"] == "" and rows["10000"]["ns"] == 4
    hansa = json.loads((app / "avail" / "1208.json").read_text())
    by_id = {w["wines"][i]["id"]: (stock, shelf) for i, stock, shelf in hansa}
    assert len(hansa) == 70 + 32 + 14 + 11 and by_id["20000"] == (None, None) and by_id["10001"] == (6, "A-01")
    assert json.loads((app / "avail" / "0400.json").read_text()) == []
    stores = {s["id"]: s for s in json.loads((app / "stores.json").read_text())}
    assert set(stores) == {"1208", "0102", "0615", "0311", "0400"} and not (app / "avail" / "0999.json").exists()
    assert stores["0102"].get("stale") and not stores["1208"].get("stale") and stores["1208"]["wines"] == len(hansa)
    assert stores["0615"]["name"] == "Storgatan 1" and stores["1208"]["town"] == "Malmö" and stores["1208"]["chk"]
    meta = json.loads((app / "meta.json").read_text())
    assert meta["stores_stale"] == 1 and meta["wines"] == len(rows) and "source_store" not in meta
    with (STATE / "matches.csv").open(newline="") as f:
        assert "4.1" not in f.read()  # tracked state holds no ratings
    assert list(csv.reader((STATE / "matches.csv").open()))[0][0] == "article"


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
