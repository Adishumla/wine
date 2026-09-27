"""Rolling Vivino rating refresh (phase 4 step 3), against the mocked api.vivino.com. No network.

Run: python -m pipeline.tests.test_ratings
"""

from __future__ import annotations

import json
from datetime import date
from urllib.parse import urlsplit

from pipeline.tests.mock import DATA, STATE, calls, fresh, handler as mock_handler  # noqa: I001 (sets up first)

import httpx  # noqa: E402

from pipeline import net, ratings, state  # noqa: E402

TODAY = "2026-09-27"
special: dict[int, object] = {}  # Vivino id -> status code, a 200 JSON body, or callable(id) -> Response
seen: list[int] = []  # Vivino ids that reached the (mock) network, in order


def handler(req: httpx.Request) -> httpx.Response:
    u = urlsplit(str(req.url))
    if u.hostname == "api.vivino.com":
        wid = int(u.path.rsplit("/", 1)[1])
        seen.append(wid)
        r = special.get(wid)
        if r is not None:
            calls["vivino"] += 1
            if callable(r):
                return r(wid)
            return httpx.Response(200, json=r) if isinstance(r, dict) else httpx.Response(r)
    return mock_handler(req)  # average 3.8 from 420 ratings for any other id


net.TRANSPORT = httpx.MockTransport(handler)


class Killed(Exception):
    pass


def setup(rows: list[dict], overrides: list[tuple[str, str]] = (), stored: dict | None = None) -> None:
    fresh("vivino")
    special.clear()
    seen.clear()
    state.write_matches(rows)
    ov = STATE / "overrides.csv"
    if overrides:
        ov.write_text("article,vivino_id,note,set_at\n" + "".join(f"{a},{v},test,{TODAY}\n" for a, v in overrides))
    else:
        ov.unlink(missing_ok=True)
    for name in (ratings.NAME, ratings.STATS):
        (DATA / name).unlink(missing_ok=True)
    if stored is not None:
        (DATA / ratings.NAME).write_text(json.dumps(stored))


def accept(*ids: int) -> list[dict]:
    return [{"article": f"9{v}", "vivino_id": str(v), "band": "accept", "matched_at": TODAY} for v in ids]


def r(avg: float | None, count: int | None, checked: str | None, **kw) -> dict:
    return {"average": avg, "count": count, "checked_at": checked, **kw}


def stored() -> dict:
    return json.loads((DATA / ratings.NAME).read_text())


def run(limit: int = 600, today: str = TODAY) -> dict:
    return ratings.refresh(net.Http("test"), limit=limit, today=today)


def test_due_per_tier() -> None:
    day = date.fromisoformat(TODAY)
    cases = [
        (None, "new", True),
        (r(None, None, None, tried_at="2026-09-20", failures=1), "new", True),  # never fetched successfully
        (r(None, 0, "2026-09-25"), "no_rating", True), (r(None, 0, "2026-09-26"), "no_rating", False),
        (r(0, 12, "2026-09-26"), "no_rating", False),  # average 0: below Vivino's threshold
        (r(4.1, 99, "2026-09-24"), "under_100", True), (r(4.1, 99, "2026-09-25"), "under_100", False),
        (r(4.1, 100, "2026-09-20"), "100_1000", True), (r(4.1, 1000, "2026-09-21"), "100_1000", False),
        (r(4.1, 1001, "2026-08-28"), "over_1000", True), (r(4.1, 25000, "2026-08-29"), "over_1000", False),
    ]
    for entry, tier, due in cases:
        assert ratings.tier(entry) == tier, entry
        assert ratings.due(entry, day) == due, entry
    # A failure keeps checked_at; the next try waits 1, 2, 4 ... 30 days after the last one.
    failing = r(4.1, 5000, "2026-01-01", tried_at="2026-09-26", failures=1)
    assert ratings.due(failing, day) and not ratings.due({**failing, "tried_at": TODAY}, day)
    assert not ratings.due({**failing, "failures": 3, "tried_at": "2026-09-24"}, day)
    assert ratings.due({**failing, "failures": 3, "tried_at": "2026-09-23"}, day)
    assert not ratings.due({**failing, "failures": 20, "tried_at": "2026-08-29"}, day)
    assert ratings.due({**failing, "failures": 20, "tried_at": "2026-08-28"}, day)


def test_order_and_budget() -> None:
    setup(accept(101, 102, 201, 202, 203, 204, 205), stored={
        "201": r(None, 0, "2026-09-20"),  # no rating yet
        "202": r(4.0, 50, "2026-09-01"),
        "203": r(3.7, 50, "2026-08-01"),  # same count, checked earlier: before 202
        "204": r(4.3, 5000, "2026-07-01"),
        "205": r(4.0, 500, "2026-09-26"),  # weekly, checked yesterday: not due
    })
    s = run(limit=4)
    assert seen == [101, 102, 201, 203], seen  # never fetched first, then low counts, then oldest check
    assert s["ids"] == 7 and s["never_fetched"] == 2 and s["not_due"] == 1 and s["backing_off"] == 0
    assert s["due"] == {"new": 2, "no_rating": 1, "under_100": 2, "100_1000": 0, "over_1000": 1}, s["due"]
    assert s["looked_up"] == s["refreshed"] == 4 and s["left"] == 2 and s["failed"] == 0
    assert s["requests"] == calls["vivino"] == 4 and s["cache_hits"] == 0
    got = stored()
    assert got["101"] == {"average": 3.8, "count": 420, "checked_at": TODAY}
    assert got["202"] == r(4.0, 50, "2026-09-01") and got["204"]["checked_at"] == "2026-07-01"  # over budget: wait
    assert json.loads((DATA / ratings.STATS).read_text()) == s
    seen.clear()
    s = run(limit=4)  # the next run takes the rest
    assert seen == [202, 204] and s["refreshed"] == 2 and s["left"] == 0
    seen.clear()
    s = run(limit=4)  # nothing due again today
    assert seen == [] and s["looked_up"] == 0 and s["not_due"] == 7
    s = run(today="2026-10-03")  # everything is weekly now (420 ratings); 205 was checked a day earlier
    assert seen == [205] and s["refreshed"] == 1


def test_failed_refresh_keeps_value_and_date() -> None:
    old = {"301": r(4.2, 50, "2026-09-01"), "302": r(3.9, 800, "2026-09-01"), "303": r(None, 3, "2026-09-01")}
    setup(accept(301, 302, 303, 304), stored=old)
    special.update({301: 404, 302: 500, 303: {"id": 303}, 304: 502})  # gone, server errors, no statistics
    s = run()
    got = stored()
    for vid in old:
        assert got[vid] == {**old[vid], "tried_at": TODAY, "failures": 1}, got[vid]
    assert got["304"] == r(None, None, None, tried_at=TODAY, failures=1)  # build.py still finds its three fields
    assert s["refreshed"] == 0 and s["failed"] == 4 and "stopped" not in s
    assert s["failed_by_status"] == {"404": 1, "500": 1, "502": 1, "no statistics": 1}, s["failed_by_status"]
    assert s["requests"] == calls["vivino"] == 6  # the 5xx were retried once (mock policy)
    fresh("vivino")
    seen.clear()
    s = run()  # same day: no retries
    assert seen == [] and s["backing_off"] == 4
    special.pop(301)
    s = run(today="2026-09-28")  # the next day each is tried again; a success clears the failures
    got = stored()
    assert sorted(seen) == [301, 302, 302, 303, 304, 304]
    assert got["301"] == {"average": 3.8, "count": 420, "checked_at": "2026-09-28"}
    assert got["302"] == {**old["302"], "tried_at": "2026-09-28", "failures": 2}


def test_server_errors_stop_the_run() -> None:
    setup(accept(*range(401, 409)))
    special.update({v: 503 for v in range(401, 409)})
    s = run()
    assert s["stopped"] and "blocked" not in s
    assert s["looked_up"] == ratings.MAX_SERVER_ERRORS == 5 and s["left"] == 3 and len(stored()) == 5


def test_blocked_stops_and_keeps_progress() -> None:
    setup(accept(*range(501, 511)))
    special.update({v: 403 for v in range(505, 511)})
    s = run()
    assert "blocked" in s and seen == list(range(501, 508))  # two 403s fail, the third blocks the group
    got = stored()
    assert {v for v, e in got.items() if e["checked_at"] == TODAY} == {"501", "502", "503", "504"}
    assert got["505"]["failures"] == got["506"]["failures"] == 1 and "507" not in got
    assert s["refreshed"] == 4 and s["failed"] == 2 and s["left"] == 4
    assert json.loads((DATA / ratings.STATS).read_text())["blocked"] == s["blocked"]


def test_killed_run_resumes() -> None:
    setup(accept(*range(601, 609)))
    on_disk: dict = {}

    def kill(wid: int) -> httpx.Response:
        on_disk.update(stored())
        raise Killed

    special[608] = kill
    every, ratings.SAVE_EVERY = ratings.SAVE_EVERY, 3
    try:
        run()
        raise AssertionError("not killed")
    except Killed:
        pass
    finally:
        ratings.SAVE_EVERY = every
    assert set(on_disk) == {str(v) for v in range(601, 607)}  # saved every 3 lookups: 607 not yet
    (DATA / ratings.NAME).write_text(json.dumps(on_disk))  # what a hard kill (no finally) leaves
    special.clear()
    seen.clear()
    s = run(limit=1)
    assert seen == [608] and s["cache_hits"] == 1 and s["requests"] == 1 and s["refreshed"] == 2  # 607: cached
    assert set(stored()) == {str(v) for v in range(601, 609)}


def test_which_ids_and_state_untouched() -> None:
    rows = [{"article": "A1", "vivino_id": "11", "band": "accept"},
            {"article": "A2", "vivino_id": "12", "band": "review"},
            {"article": "A3", "vivino_id": "13", "band": "reject"},
            {"article": "A4", "vivino_id": "14", "band": "accept"},  # overridden: none
            {"article": "A5", "vivino_id": "15", "band": "accept"},  # overridden: another id
            {"article": "A6", "vivino_id": "", "band": "accept"}]
    old = {"12": r(4.4, 30, "2026-01-01"), "99": r(4.2, 50, "2026-01-01")}  # no longer shown: kept as they are
    setup(rows, overrides=[("A4", "none"), ("A5", "16"), ("B1", "17"), ("B2", "NONE")], stored=old)
    before = {p.name: p.read_bytes() for p in STATE.iterdir()}
    s = run()
    assert sorted(seen) == [11, 16, 17] and s["ids"] == 3 and s["unmatched_kept"] == 2
    got = stored()
    assert set(got) == {"11", "12", "16", "17", "99"} and got["12"] == old["12"] and got["99"] == old["99"]
    assert {p.name: p.read_bytes() for p in STATE.iterdir()} == before  # ratings go to data/, never state/
    assert not list(DATA.glob("*.tmp"))


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
