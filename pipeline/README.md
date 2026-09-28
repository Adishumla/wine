# pipeline

Every store's wines with stock and shelf, matched to Vivino, built into the app's data files.

```bash
.venv/bin/python -m pipeline.run fetch          # every store's wines, stock and shelf -> data/assortment.json
.venv/bin/python -m pipeline.run orders         # the order-only range -> data/orders.json (--max-age 6: weekly)
.venv/bin/python -m pipeline.run photos         # bottle photos for wines without one -> data/app/img/
.venv/bin/python -m pipeline.run match          # new or changed wines, due re-searches (--limit N per run)
.venv/bin/python -m pipeline.run refresh        # ratings due on the rolling schedule (--limit, default 600)
.venv/bin/python -m pipeline.run report         # coverage and precision per assortment and cohort
.venv/bin/python -m pipeline.run audit          # every group that still needs hand checks
open data/review.html                           # R / W / U, then "Copy answers"
pbpaste | .venv/bin/python -m pipeline.run labels
.venv/bin/python -m pipeline.run reports        # "Wrong match?" issues -> state/ (needs gh; --close closes resolved ones)
.venv/bin/python -m pipeline.run queue          # data/queue.html: reports, then the review band by reach (--n 50)
open data/queue.html                            # 1 / 2 / W / N / U, L for a link, then "Copy answers"
pbpaste | .venv/bin/python -m pipeline.run labels
.venv/bin/python -m pipeline.run build          # data/app/ for the app (app/README.md)
```

Steps:

1. `fetch` lists every active store's wines with stock and shelf (`assortment.py` explains the method): per store one search page for its per-assortment counts and upcoming launches, the in-store range sliced by type, each store's order-only wines, then `site/stores/{productId}` once per wine. Each store's list must add up to its counts per assortment and per type; a part that doesn't is fetched directly (as narrowly as the gap allows, within a page budget, longest-unchecked stores first), and a store that still doesn't keeps its previous list ("stale"). Two random stores are fetched in full every run as a spot check. Stock rows for wines not launched yet that a store doesn't list yet are deliveries ahead of the launch: listed as arriving, not counted. A range slice that stays short or a wine without stock data fails the step and keeps the previous file. Exit code 2 when a few stores (up to 10 %) stay stale, 1 on failure. Output: `data/assortment.json`.
2. `match` applies `state/overrides.csv` first and reuses every stored row whose wine name, vintage and matcher version are unchanged; only new or changed wines go through the matcher (`matching.py`), widely carried wines first, up to `--limit`. Wines not found on Vivino are searched again with fresh responses: weekly for two months after launch, then monthly. Outputs: `state/matches.csv`, `data/review_queue.csv`, `data/match_details.json`, `data/match.json` (this run's counts, including accept/review/reject of the new wines per assortment).
3. `refresh` (`ratings.py`) fetches ratings by Vivino id for every accept and override on a rolling schedule, low counts first: never fetched now, no rating yet every 2 days, under 100 ratings every 3, up to 1,000 weekly, above that monthly. At most `--limit` network lookups per run (600 by default). A failed lookup keeps the last value and its date and is retried after 1, 2, 4 … 30 days; repeated 403/429s or 5 server errors in a row stop the run. Outputs: `data/ratings.json`, `data/refresh.json`.
4. `report` shows coverage and precision per group, and how many more hand checks each group needs for "≥ 95 % right at a 95 % lower bound". A group is an assortment within a cohort: "Fast sortiment" is the wines audited in phase 2, "Fast sortiment (new)" those first matched from phase 4 on. Output: `data/report.md`.
5. `build` turns local state into the app's data files, with no network: `data/assortment.json`, the tracked matches, overrides and labels, and `data/ratings.json`. An accept shows its rating once its group meets the bar or Adam checked it right; until then it goes out as "unchecked", with no rating. A review-band match checked right shows its rating too ("checked"); a match reported wrong and not resolved shows none ("reported"). Output: `data/app/` (`wines.json`, `avail/<store>.json`, `stores.json`, `meta.json`).
6. `photos` (`photos.py`) copies a 60 px bottle photo per wine from Systembolaget's image CDN onto the site, widely carried wines first, up to `--limit` a run (1,500); a wine without one is tried again after 30 days, and photos of wines no store carries any more are removed. Output: `data/app/img/`.
7. `reports` (`reports.py`, on the Mac with `gh` signed in) reads the app's "Wrong match?" issues into `state/reports.csv`: the reported pairing is labelled wrong, a pasted Vivino link or "none" becomes an override, and a report is resolved as fixed or kept by later answers. `--close` closes the resolved issues.
8. `queue` (`worklist.py`) builds `data/queue.html`: open reports, then the review band nobody has checked, most widely carried first; `labels` merges its answers (verdicts, and overrides for another wine or none). Weekly: `reports`, `queue`, answer, `labels`, commit and push `state/`.
9. `orders` (`orders.py`, phase 7) fetches the whole order-only range (Ordervaror) nationwide, sliced by type, in sorted passes until unique = docCount, with no stock calls; with `--max-age 6` it skips while the last fetch is under 6 days old (the nightly runs it that way: weekly). Matching, photos, the queue and the build then work on the catalog: the stores' wines, then the order-only wines no store carries (cohort "p7", group "Ordervaror (order-only)", matched after the store wines). Output: `data/orders.json`.

Files:

| Path | In git | Holds |
|---|---|---|
| `state/matches.csv` | yes | per article: Vivino id, band, scores, what it was matched on, matcher version, match and search dates, cohort |
| `state/overrides.csv` | yes | article → Vivino id, or `none` for "no rating"; always wins |
| `state/labels.csv` | yes | Adam's verdicts on (article, Vivino id) pairs, the only ground truth (with checks he delegates, marked in the note) |
| `state/reports.csv` | yes | "Wrong match?" reports: issue, article, reported and suggested Vivino ids, status (open, fixed, kept, withdrawn) |
| `data/` | no | cache, every store's wines, review queue, evidence, ratings, report; a symlink to `~/Library/Caches/wine-v2/data` |

Tracked files hold ids, verdicts and dates only, never Vivino names or ratings.

Every response is cached in `data/cache/`. Algolia queries are reused indefinitely (a due re-search takes responses under a day old), the stores' assortments and stock for 12 hours. Re-matching after a change to `matching.py` costs no network for queries already asked.

Tests (no network): `python -m pipeline.tests.test_pipeline`, `test_assortment`, `test_ratings`, `test_logs`, `test_matching`, `test_net`, `test_photos`, `test_orders`. `tests/mock.py` is the shared mocked Systembolaget (five stores), Algolia and api.vivino.com. `test_logs` feeds canary Vivino names, ratings and keys through every step and error path and fails if any reaches the output: the nightly's logs are public.

## Nightly and the public repo

The nightly (`public/.github/workflows/nightly.yml`) runs in the public code repo `Adishumla/wine`, which `tools/publish.py` fills from committed files here (a whitelist, checked for Vivino ids, keys, hosts and local paths). It checks out `state/` from this repo with a deploy key and commits it back, so `state/matches.csv` has two writers: pull before matching, labelling or editing overrides here, and don't push `state/` while a run is going. Match evidence for wines matched on GitHub stays in its encrypted cache; run `match` here before auditing them.
