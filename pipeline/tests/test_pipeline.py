"""Matching every store's wines, the report, the audit and the build gate, against the shared mock. No network.

Run: python -m pipeline.tests.test_pipeline
"""

from __future__ import annotations

import csv
import datetime
import gzip
import json

from pipeline.tests.mock import BY_ID, DATA, STATE, calls, fresh, knobs  # noqa: I001 (sets up first)

from pipeline import assortment, match as match_step, run, state  # noqa: E402
from pipeline.report import group_of, groups  # noqa: E402

TODAY = datetime.date.today().isoformat()


def read_csv(path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def matches() -> dict[str, dict]:
    return {r["article"]: r for r in read_csv(STATE / "matches.csv")}


def wines() -> dict[str, dict]:
    return {str(p["productNumber"]): p for p in json.loads((DATA / assortment.OUT).read_text())["wines"].values()}


def last_run() -> dict:
    return json.loads((DATA / "match.json").read_text())


def age_cache(group: str, seconds: float) -> None:
    """Make every cached response of a host group look this much older."""
    for f in (DATA / "cache" / "http" / group).glob("*.json.gz"):
        with gzip.open(f, "rt") as fh:
            c = json.load(fh)
        c["fetched_at"] -= seconds
        with gzip.open(f, "wt") as fh:
            json.dump(c, fh)


def test_phase2_rows_keep_cohort_and_date() -> None:
    fresh()
    knobs["local_gap"] = False
    assert run.main(["fetch"]) == 0
    # A phase 2 file (no searched_at or cohort columns) with three of today's wines, matched by this matcher.
    ws = wines()
    old = sorted(ws)[:3]
    with (STATE / "matches.csv").open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["article", "product_id", "assortment", "sb_name", "sb_vintage", "vivino_id", "band",
                    "producer_score", "name_score", "total_score", "matcher_version", "matched_at"])
        for a in old:
            p = ws[a]
            w.writerow([a, p["productId"], p["assortmentText"], p["productNameBold"], p["vintage"], "", "review",
                        "", "", "", match_step.MATCHER_VERSION, "2026-09-27"])
    assert run.main(["match"]) == 0
    rows = matches()
    assert len(rows) == len(ws)
    assert all(rows[a]["cohort"] == "p2" and rows[a]["matched_at"] == "2026-09-27" for a in old)
    assert {r["cohort"] for a, r in rows.items() if a not in old} == {"p4"}
    m = last_run()
    assert m["kept"] == 3 and m["matched"] == {"new": len(ws) - 3} and m["left_for_next_run"] == 0
    assert sum(sum(b.values()) for b in m["new_by_assortment"].values()) == len(ws) - 3
    accepted = [r for r in rows.values() if r["band"] == "accept"]
    assert accepted and all(int(r["vivino_id"]) < 9_000_000 for r in accepted), accepted[:3]
    assert calls["vivino"] == 0  # ratings are step 3's job
    tracked = (STATE / "matches.csv").read_text()
    assert "Decoy Winery" not in tracked and "3.9" not in tracked  # no Vivino names or ratings in git


def test_only_new_or_changed_wines_are_matched() -> None:
    rows = matches()
    for r in rows.values():
        r["matched_at"] = r["searched_at"] = "2026-01-01" if r["band"] != "reject" else TODAY
    state.write_matches(list(rows.values()))
    calls.clear()
    assert run.main(["match"]) == 0
    assert last_run()["matched"] == {} and calls["algolia"] == 0
    # One wine is renamed at Systembolaget: only that one is matched again.
    pid = "10002"
    BY_ID[pid]["productNameBold"] += " Riserva"
    try:
        fresh()
        assert run.main(["fetch"]) == 0
        assert run.main(["match"]) == 0
        assert last_run()["matched"] == {"changed": 1} and calls["algolia"] > 0
        after = matches()
        art = str(int(pid) + 1000)
        assert after[art]["sb_name"].endswith("Riserva") and after[art]["searched_at"] == TODAY
        assert all(r["matched_at"] == "2026-01-01" for a, r in after.items() if a != art and r["band"] != "reject")
    finally:
        BY_ID[pid]["productNameBold"] = BY_ID[pid]["productNameBold"].removesuffix(" Riserva")
        fresh()
        assert run.main(["fetch"]) == 0
        assert run.main(["match"]) == 0 and last_run()["matched"] == {"changed": 1}


def test_rejects_are_searched_again_on_schedule() -> None:
    today = datetime.date(2026, 12, 1)
    due = match_step.research_due
    assert due({"searched_at": "2026-11-24"}, "2026-11-01", today)  # launched a month ago: weekly
    assert not due({"searched_at": "2026-11-25"}, "2026-11-01", today)
    assert not due({"searched_at": "2026-11-02"}, "2026-06-01", today)  # older launch: monthly
    assert due({"searched_at": "2026-11-01"}, "2026-06-01", today)
    assert due({"matched_at": "2026-10-01"}, None, today)  # no searched_at yet: the match date counts
    rows = matches()
    rejects = [a for a, r in rows.items() if r["band"] == "reject"]
    assert rejects, "the mock should produce some rejects"
    rows[rejects[0]]["searched_at"] = "2026-01-01"
    state.write_matches(list(rows.values()))
    age_cache("vivino", 2 * 86400)
    calls.clear()
    assert run.main(["match"]) == 0
    assert last_run()["matched"] == {"research": 1}
    assert calls["algolia"] > 0  # fresh responses, although the same queries are cached (2 days old)
    assert matches()[rejects[0]]["searched_at"] == TODAY


def test_limit_matches_widely_carried_wines_first() -> None:
    saved = (STATE / "matches.csv").read_text()
    (STATE / "matches.csv").unlink()
    try:
        assert run.main(["match", "--limit", "5"]) == 0
        m = last_run()
        assert m["matched"] == {"new": 5} and m["left_for_next_run"] == len(wines()) - 5
        ns = sorted((p["ns"] for p in wines().values()), reverse=True)
        assert sorted((wines()[a]["ns"] for a in matches()), reverse=True) == ns[:5]
    finally:
        (STATE / "matches.csv").write_text(saved)


def test_overrides_labels_and_matcher_changes() -> None:
    first = matches()
    accepted = [a for a, r in first.items() if r["band"] == "accept"]
    with (STATE / "overrides.csv").open("w") as f:
        f.write(f"article,vivino_id,note,set_at\n{accepted[0]},none,wrong wine,2026-09-27\n"
                f"{accepted[1]},12345,right wine,2026-09-27\n")
    assert state.add_labels([{"article": accepted[2], "vivino_id": first[accepted[2]]["vivino_id"],
                              "verdict": "right", "note": ""}]) == 1
    assert run.main(["match"]) == 0
    again = matches()
    assert again[accepted[0]]["band"] == "override" and again[accepted[0]]["vivino_id"] == ""
    assert again[accepted[1]]["band"] == "override" and again[accepted[1]]["vivino_id"] == "12345"
    assert again[accepted[3]]["matched_at"] == first[accepted[3]]["matched_at"]
    assert (accepted[2], first[accepted[2]]["vivino_id"]) in state.labels()
    match_step.MATCHER_VERSION, old = "test-version", match_step.MATCHER_VERSION
    try:  # a matcher change re-decides every row and re-dates the ones whose result changed
        assert run.main(["match"]) == 0
        assert last_run()["matched"].get("changed") == len(wines()) - 2
        assert {r["matcher_version"] for r in matches().values() if r["band"] != "override"} == {"test-version"}
    finally:
        match_step.MATCHER_VERSION = old
    assert run.main(["match"]) == 0


def test_vivino_block_keeps_previous_rows() -> None:
    before = matches()
    fresh("vivino")
    knobs["vivino_403"] = True
    match_step.MATCHER_VERSION, old = "another-version", match_step.MATCHER_VERSION
    try:
        assert run.main(["match"]) == 1
        assert "blocked" in last_run()
        after = matches()
        assert set(after) == set(before)  # nothing lost, nothing half-written
        assert all(after[a] == r for a, r in before.items() if r["band"] != "override")
    finally:
        knobs["vivino_403"] = False
        match_step.MATCHER_VERSION = old
        fresh("vivino")
    assert run.main(["match"]) == 0


def test_audit_page_and_labels() -> None:
    g = "Fast sortiment (new)"
    assert g in groups() and groups()[g]["accept"] >= 5
    assert run.main(["audit", "--group", g, "--n", "5"]) == 0
    page = (DATA / "review.html").read_text()
    assert "vivino.com/w/" in page and "vivino.com/wines/" not in page
    items = json.loads(page.split("const ITEMS = ", 1)[1].split(";\n", 1)[0].replace("<\\/", "</"))
    assert len(items) == 5 and all(i["sb"]["Assortment"] == g for i in items)
    from pipeline import review
    text = "article,vivino_id,verdict,note\n" + "\n".join(f'{i["article"]},{i["vivino_id"]},right,""' for i in items)
    assert review.import_answers(text) == 5
    assert run.main(["report"]) == 0 and f"| {g} |" in (DATA / "report.md").read_text()
    need = {k: v["more_to_check"] for k, v in groups().items() if v["accept"]}
    assert run.main(["audit"]) == 0  # no group: every group that still needs checks, small groups in full
    page = (DATA / "review.html").read_text()
    items = json.loads(page.split("const ITEMS = ", 1)[1].split(";\n", 1)[0].replace("<\\/", "</"))
    assert len(items) == sum(need.values()), (len(items), need)


def test_build_shows_new_ratings_once_checked() -> None:
    rows = matches()
    labels = state.labels()
    accepted = [r for r in rows.values() if r["band"] == "accept"]
    (DATA / "ratings.json").write_text(json.dumps({r["vivino_id"]: {"average": 4.0, "count": 300,
                                                                    "checked_at": TODAY} for r in accepted}))

    def built() -> dict[str, dict]:
        assert run.main(["build"]) == 0
        return {w["art"]: w for w in json.loads((DATA / "app" / "wines.json").read_text())["wines"]}

    b = built()
    for r in accepted:  # only the ones Adam called right have a rating so far: no group is fully checked
        right = labels.get((r["article"], r["vivino_id"]), {}).get("verdict") == "right"
        assert (b[r["article"]]["band"], b[r["article"]]["rat"]) == (("accept", 4.0) if right else ("unchecked", None))
    # Check every accept of the smallest group with accepts: the group meets the bar, its ratings show.
    g = min((k for k, v in groups().items() if v["accept"]), key=lambda k: groups()[k]["accept"])
    todo = [r for r in accepted if group_of(r) == g]
    state.add_labels([{"article": r["article"], "vivino_id": r["vivino_id"], "verdict": "right", "note": ""}
                      for r in todo])
    assert groups()[g]["meets_target"]
    b = built()
    assert all(b[r["article"]]["rat"] == 4.0 for r in todo)
    state.add_labels([{"article": todo[0]["article"], "vivino_id": todo[0]["vivino_id"], "verdict": "wrong",
                       "note": ""}])
    b = built()
    assert b[todo[0]["article"]]["band"] == "review" and b[todo[0]["article"]]["rat"] is None


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
