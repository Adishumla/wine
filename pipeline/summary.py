"""One run's summary as Markdown, for the Actions job summary and pipeline/REPORT.md. Counts only: no wine names,
no Vivino names, no ratings.

    python -m pipeline.summary [--since EPOCH]    # requests since EPOCH (default: the whole requests.jsonl)
"""

from __future__ import annotations

import argparse
import collections
import json
import statistics

from .net import DATA, LOG


def _load(name: str, since: float) -> dict:
    """The step's output, if this run wrote it."""
    path = DATA / name
    return json.loads(path.read_text()) if path.exists() and path.stat().st_mtime >= since else {}


def _alias(host: str) -> str:
    """A host's role, not its name (the Algolia host name holds an app id)."""
    for end, name in ((".algolia.net", "algolia (Vivino search)"), (".algolianet.com", "algolia (Vivino search)"),
                      ("api.vivino.com", "api.vivino.com"), ("api-extern.systembolaget.se", "systembolaget api"),
                      ("systembolaget.se", "systembolaget site"), ("githubusercontent.com", "github raw")):
        if host.endswith(end):
            return name
    return "other"


def hosts(since: float = 0.0) -> list[dict]:
    """Per host: requests, statuses, 429/403 counts, median and p90 latency, and 429 pauses."""
    per: dict[str, dict] = collections.defaultdict(lambda: {"statuses": collections.Counter(), "ms": [], "pauses": 0})
    if LOG.exists():
        for line in LOG.open():
            r = json.loads(line)
            if r["ts"] < since:
                continue
            h = per[_alias(r.get("host") or r.get("group", "?"))]
            if r.get("event") == "pause":
                h["pauses"] += 1
                continue
            h["statuses"][str(r.get("status") or r.get("error", "error")[:20])] += 1
            if r.get("ms") is not None:
                h["ms"].append(r["ms"])
    out = []
    for host, h in sorted(per.items()):
        ms = sorted(h["ms"])
        out.append({"host": host, "requests": sum(h["statuses"].values()), "statuses": dict(h["statuses"]),
                    "429": h["statuses"].get("429", 0), "403": h["statuses"].get("403", 0), "pauses": h["pauses"],
                    "median_ms": round(statistics.median(ms)) if ms else None,
                    "p90_ms": round(ms[int(0.9 * (len(ms) - 1))]) if ms else None})
    return out


def markdown(since: float = 0.0) -> str:
    a, m, r = (_load(n, since) for n in ("assortment.json", "match.json", "refresh.json"))
    lines = ["## Nightly run", ""]
    if a:
        c = a.get("counts", {})
        lines += [f"**Stock:** {c.get('wines')} wines in {c.get('stores')} "
                  f"stores ({json.dumps(c.get('by_status'))}), {c.get('store_wine_pairs')} store-wine pairs, "
                  f"{c.get('no_row')} not stocked yet, {c.get('crawled_parts')} parts fetched directly; "
                  f"{a.get('seconds')} s ({json.dumps(a.get('timing'))}).", ""]
        bad = {sid: s["gaps"] for sid, s in a.get("stores", {}).items() if s["status"] in ("stale", "incomplete")}
        if bad:
            lines += [f"Stores that didn't add up: `{json.dumps(bad, ensure_ascii=False)[:1500]}`", ""]
    if m:
        lines += [f"**Match:** {json.dumps(m.get('matched'))}, {m.get('kept')} kept, {m.get('left_for_next_run')} left "
                  f"for the next run, {m.get('algolia_queries')} Algolia queries"
                  + (f", BLOCKED: {m['blocked']}" if m.get("blocked") else "") + ".", "",
                  f"New wines by band: `{json.dumps(m.get('new_by_assortment'), ensure_ascii=False)}`", ""]
    if r:
        lines += [f"**Ratings:** {r.get('ids')} ids, due {json.dumps(r.get('due'))}, {r.get('refreshed')} refreshed, "
                  f"{r.get('failed')} failed, {r.get('left')} left, {r.get('requests')} requests"
                  + "".join(f", {k.upper()}: {r[k]}" for k in ("blocked", "stopped") if r.get(k)) + ".", ""]
    hs = hosts(since)
    if hs:
        lines += ["| Host | Requests | Statuses | 429 | 403 | Pauses | Median | p90 |", "|---|---|---|---|---|---|---|---|"]
        lines += [f"| {h['host']} | {h['requests']} | {json.dumps(h['statuses'])} | {h['429']} | {h['403']} | "
                  f"{h['pauses']} | {h['median_ms']} ms | {h['p90_ms']} ms |" for h in hs]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser(prog="pipeline.summary")
    ap.add_argument("--since", type=float, default=0.0)
    print(markdown(ap.parse_args().since))
