"""Nothing Vivino-side reaches the output: the nightly runs in a public repo, so its logs and job summary are public.

Every step and the summary run with stdout and stderr captured, on normal data and on error paths (Algolia 500
with a body, repeated 403s, a crash in CI), and the output must hold none of the canary values: Vivino names and
regions, ratings and counts, response bodies, the keys, or decoy Vivino ids.

Run: python -m pipeline.tests.test_logs
"""

from __future__ import annotations

import contextlib
import io
import os
import re

from pipeline.tests.mock import CANARY, DATA, KEY, fresh, knobs, reset_knobs  # noqa: I001 (sets up first)

from pipeline import build, match as match_step, run, summary  # noqa: E402

SECRETS = [KEY, "fedcba9876543210fedcba9876543210", "TESTAPP123", "testapp123"]
DECOY_ID = re.compile(r"(?<!\d)90\d{5}(?!\d)")  # the mock's other-winery hits: 9,000,000 + product id


def quiet(argv: list[str]) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = run.main(argv)
    return code, out.getvalue() + err.getvalue()


def clean(text: str) -> None:
    for v in [CANARY["winery"], CANARY["region"], str(CANARY["average"]), str(CANARY["count"]), CANARY["body"],
              "Sentinel", *SECRETS]:
        assert v not in text, (v, text[:2000])
    assert not DECOY_ID.search(text), DECOY_ID.search(text).group(0)


def test_normal_run() -> None:
    fresh()
    knobs["local_gap"] = False
    text = ""
    for argv in (["fetch"], ["orders"], ["match"], ["refresh"], ["build"], ["report"]):
        code, out = quiet(argv)
        assert code == 0, (argv, out)
        text += out if argv != ["report"] else ""  # the report is a local file, not a log (it names nothing either)
    clean(text + summary.markdown())


def test_error_paths() -> None:
    match_step.MATCHER_VERSION, old = "canary-version", match_step.MATCHER_VERSION  # re-match everything
    fresh("vivino")
    knobs["algolia_500"] = True
    try:
        code, out = quiet(["match"])
    finally:
        reset_knobs()
    assert code == 1 and "HTTP 500" in out
    clean(out + summary.markdown())
    fresh("vivino")
    (DATA / "ratings.json").unlink()  # every rating due again
    knobs["vivino_403"] = True
    try:
        code_m, out_m = quiet(["match"])
        code_r, out_r = quiet(["refresh", "--limit", "5"])
    finally:
        reset_knobs()
        match_step.MATCHER_VERSION = old
    assert code_m == 1 and code_r == 1
    clean(out_m + out_r + summary.markdown())


def test_ci_crash_prints_type_and_place_only() -> None:
    def boom() -> dict:
        raise ValueError(f"{CANARY['winery']} rated {CANARY['average']}")

    build.build, real = boom, build.build
    os.environ["GITHUB_ACTIONS"] = "true"
    try:
        code, out = quiet(["build"])
    finally:
        build.build = real
        del os.environ["GITHUB_ACTIONS"]
    assert code == 1 and "ValueError at test_logs.py" in out, out
    clean(out)


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
