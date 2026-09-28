"""Bottle photos copied onto the site (phase 5), against the mocked image CDN. No network.

Run: python -m pipeline.tests.test_photos
"""

from __future__ import annotations

import datetime
import json

from pipeline.tests.mock import DATA, WEBP, calls, fresh, knobs, reset_knobs  # noqa: I001 (sets up first)

from pipeline import photos, run  # noqa: E402
from pipeline.build import expand  # noqa: E402

IMG = DATA / "app" / "img"


def wines() -> dict:
    return json.loads((DATA / "assortment.json").read_text())["wines"]


def test_fills_in_widely_carried_first_and_remembers_404s() -> None:
    fresh()
    knobs["local_gap"] = False
    assert run.main(["fetch"]) == 0
    ws = wines()
    knobs["photo_404"] = {"10000"}
    try:
        assert run.main(["photos", "--limit", "10"]) == 0
        first = json.loads((DATA / "photos_run.json").read_text())
        assert first["downloaded"] + first["no_photo"] == 10 and first["left"] == len(ws) - 10
        ns = sorted((w["ns"] for w in ws.values()), reverse=True)
        got = {f.stem for f in IMG.glob("*.webp")} | ({"10000"} if first["no_photo"] else set())
        assert sorted((ws[p]["ns"] for p in got), reverse=True) == ns[:10]  # widely carried first
        assert all(f.read_bytes() == WEBP for f in IMG.glob("*.webp"))
        calls.clear()
        assert run.main(["photos"]) == 0  # the rest; the 404 isn't asked again
        assert calls["photo"] == len(ws) - 10
        assert len(list(IMG.glob("*.webp"))) == len(ws) - 1
        assert json.loads((DATA / "photos.json").read_text())["10000"]["status"] == 404
        calls.clear()
        assert run.main(["photos"]) == 0 and calls["photo"] == 0  # nothing left to do
        later = datetime.date.today() + datetime.timedelta(days=photos.RETRY_DAYS)
        photos.run(run.Http("test"), today=later)  # a month on, the 404 is tried again
        assert calls["photo"] == 1
    finally:
        reset_knobs()


def test_removed_wines_lose_their_photo_and_build_flags() -> None:
    (IMG / "99999999.webp").write_bytes(WEBP)  # a wine no store carries any more
    assert run.main(["photos"]) == 0 and not (IMG / "99999999.webp").exists()
    assert run.main(["build"]) == 0
    rows = {w["id"]: expand(w) for w in json.loads((DATA / "app" / "wines.json").read_text())["wines"]}
    assert rows["10001"]["img"] == 1 and rows["10000"]["img"] == 0


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
