"""Pipeline command line.

    python -m pipeline.run fetch                    # every store's wines, stock and shelf: data/assortment.json
    python -m pipeline.run match [--limit N]        # new or changed wines, due re-searches: state/matches.csv
    python -m pipeline.run refresh [--limit 600]    # ratings due on the rolling schedule: data/ratings.json
    python -m pipeline.run report                   # coverage and precision per assortment and cohort
    python -m pipeline.run audit                    # data/review.html: every group that still needs checks
    python -m pipeline.run audit --group "Tillfälligt sortiment (new)" --n 59   # or one group
    python -m pipeline.run labels < answers.csv     # merge the review page's answers into state/labels.csv
    python -m pipeline.run build                    # data/app/: the app's data files, from local state only

Exit code 1 when a step fails, when Vivino blocks or fails match or refresh, or when fetch leaves more than 10 %
of the stores stale or incomplete (their output is still written); 2 when fetch leaves a few stores stale or
incomplete: a warning, the rest is fresh.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from pathlib import Path

from . import assortment, build, match, ratings, report, review
from .net import DATA, Blocked, Http, Offline, group_stats


PARTIAL_OK = 0.10  # share of stores that may stay stale for fetch to count as a partial success (exit 2)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="pipeline.run")
    ap.add_argument("step", choices=["fetch", "match", "refresh", "report", "audit", "labels", "build"])
    ap.add_argument("--limit", type=int, help="match: at most this many wines; refresh: lookups (default 600)")
    ap.add_argument("--group", help="audit: one group, e.g. 'Fast sortiment (new)' (default: all that need it)")
    ap.add_argument("--n", type=int, default=59, help="audit: wines to check")
    args = ap.parse_args(argv)
    http = Http(args.step)
    code = 0
    try:
        if args.step == "fetch":
            a = assortment.fetch(http)
            c = a["counts"]
            print(f"fetch: {c['wines']} wines in {c['stores']} stores ({json.dumps(c['by_status'])}), "
                  f"{c['store_wine_pairs']} store-wine pairs, {a['seconds']} s", flush=True)
            bad = {sid: s["gaps"] for sid, s in a["stores"].items() if s["status"] in ("stale", "incomplete")}
            if bad:
                print(f"fetch: {len(bad)} stores don't add up: {json.dumps(dict(list(bad.items())[:10]))}",
                      file=sys.stderr)
                code = 2 if len(bad) <= PARTIAL_OK * len(a["stores"]) else 1
        if args.step == "match":
            m = match.run(http, limit=args.limit)
            print(f"match: {json.dumps({k: m[k] for k in ('matched', 'kept', 'left_for_next_run', 'algolia_queries', 'new_by_assortment')}, ensure_ascii=False)}"
                  + "".join(f" {k.upper()}: {m[k]}" for k in ("blocked", "error") if k in m), flush=True)
            code = 1 if "blocked" in m or "error" in m else code
        if args.step == "refresh":
            r = ratings.refresh(http, limit=args.limit if args.limit is not None else 600)
            print(f"refresh: {json.dumps({k: r[k] for k in ('ids', 'due', 'refreshed', 'failed', 'left', 'requests') if k in r})}"
                  + "".join(f" {k.upper()}: {r[k]}" for k in ("blocked", "stopped") if k in r), flush=True)
            code = 1 if "blocked" in r or "stopped" in r else code
        if args.step == "report":
            print(report.write())
        if args.step == "build":
            meta = build.build()
            print(f"build: {meta['wines']} wines ({meta['rated']} rated), {meta['stores_with_wines']} stores "
                  f"({meta['stores_stale']} stale) -> data/app/")
        if args.step == "audit":
            n, path = review.build(args.group, args.n)
            print(f"{n} wines -> {path}")
        if args.step == "labels":
            print(f"labels new or changed: {review.import_answers(sys.stdin.read())}")
    except (Blocked, assortment.Incomplete, Offline) as e:
        print(f"FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        if not os.environ.get("GITHUB_ACTIONS"):
            raise
        # The public repo's logs are public: the type and where, never the message or the values in it. The full
        # traceback goes to data/, which is only ever cached encrypted.
        where = traceback.extract_tb(e.__traceback__)[-1]
        (DATA / "last_error.txt").write_text(traceback.format_exc())
        print(f"FAILED: {type(e).__name__} at {Path(where.filename).name}:{where.lineno} (details in data/last_error.txt)",
              file=sys.stderr)
        return 1
    finally:
        stats = group_stats()
        if stats:
            print(f"http: {json.dumps(stats)}", file=sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
