# app

Pick a Systembolaget store, see its wines with Vivino ratings, filter and sort on a phone. One static page, plain JS modules, no build tools. v1 in phase 3; phase 5 added photos, offline use, the home-screen app, quick picks, the value sort, favourite stores and store compare.

```bash
.venv/bin/python -m pipeline.run build                              # data/app/ from local state, no network
.venv/bin/python -m http.server 8765 --bind 127.0.0.1 --directory app   # then open http://127.0.0.1:8765
```

`app/data` is a symlink to `../data/app` (gitignored: it holds Vivino ratings). Bind to 127.0.0.1: the page serves Vivino data, so it stays off the network. To try it on the phone over Wi-Fi, bind to the Mac's LAN address for the session and stop the server afterwards.

| File | Does |
|---|---|
| `index.html` | page shell: header (store, search, filter chips), list, store and detail sheets |
| `data.js` | loads `wines.json`, `stores.json`, `meta.json` once, and `avail/<store>.json` per store; expands lookups; builds search text |
| `vlist.js` | windowed list on the page scroll: fixed 76 px rows, only visible rows (+6 each side) in the DOM |
| `app.js` | filters, quick picks, sorts, store picker with favourites and compare, detail, remembered store, filters and favourites (localStorage) |
| `sw.js` | service worker: offline shell, data and photos (below) |
| `manifest.webmanifest`, `icons/` | home-screen app; icons drawn by `tools/make_icons.py` |
| `style.css` | mobile-first, light and dark |

Data from the build step (`pipeline/build.py`):

| File | Size (27 Sep) | Holds |
|---|---|---|
| `wines.json` | 728 kB | 2,231 wines; countries, regions, grapes etc. as lookup tables; rating, count, adjusted rating and check date only for accept/override; `img` when the site has the photo; `val` (value, below) |
| `avail/<store>.json` | 5–45 kB each, 454 stores | `[wine index, stock, shelf]`; stock `null` = in the assortment, no stock row yet (upcoming launch, order-only wine not delivered) |
| `stores.json` | 60 kB | id, name (street address when the alias is just the number), town, address, position, wine count, date its list was checked, `stale` (previous list kept) or `partial` (today's list, known incomplete) |
| `meta.json` | – | schema, build time, counts, stock and rating dates, matcher version |
| `img/<article>.webp` | ~2 kB each | 60 px bottle photos, copied by the nightly `photos` step (up to 1,500 a night, widely carried wines first) |

Rules the app keeps:

- Bottle photos: rows use the site's own copy (`data/img/`, so they work offline) and fall back to Systembolaget's image CDN at 60 px; the detail loads 200 px from the CDN and falls back to the small one. A wine without a photo gets an empty slot. The page sends no referrer.
- Offline (`sw.js`): the shell is saved as one set per deploy (the deploy stamps `__VERSION__`, so a new deploy installs a whole new set and old and new files never mix; unstamped, locally, it goes to the network first). Data files: network first, the saved copy when offline or signed out, and the page then says it's showing saved data. Photos: saved once seen. A redirect to the Access login is never saved and always reaches the browser.
- Quick picks (one tap, tap again to clear): best under 150 kr, top rated here, best value, new in the last 30 days, crowd favourites (10,000+ ratings), hidden gems (4.0+ with under 500 ratings).
- Value: adjusted rating minus the median of the same type within ±15 % of its price per 75 cl, from at least 5 rated peers (`pipeline/build.py`); "Best value" sorts by it.
- "Order only" in the store picker (phase 7): the whole order-only range (`avail/orders.json`), ordered on systembolaget.se and picked up in any store. No stock or shelf; a row says "Order to any store", or how many stores also shelve it; "In stock" counts an order wine as available.
- Favourite stores (☆ in the picker, kept in localStorage) sit on top, with "Any of my stores": every wine one of them carries, stock summed, and the detail lists each favourite's stock and shelf. "Compare stores for your filters" counts matching wines in the favourites and, if location is allowed (asked only then), the 10 nearest stores.
- A rating shows only for an accepted or overridden match, or one checked by hand (band "checked": the matcher had it in review). Review, reject, unmatched, reported and "too few ratings on Vivino" all show "no rating" with the reason in the detail.
- The Vivino link (`vivino.com/w/{id}`) appears only for those same matches.
- Default sort is the adjusted rating (n·r + m·C)/(n + m), C = 3.9, m = 100; ties by rating count, then name.
- Volumes other than 750 ml are shown in the list: half bottles and boxes sit next to bottles.
- Every store lists its full wine assortment. A store whose last fetch didn't add up shows its previous list with a note and that list's date, or, with no previous list, today's with a note that it may be incomplete.
- A wine that hasn't launched yet shows "Arrives <date>", even when bottles are already in the store (they can't be sold before launch day), and doesn't count as in stock.
- A likely match in a group of new wines that isn't hand-checked yet shows "no rating" ("Match not checked yet").
- "Wrong match?" (for a wine with a rating) or "Know it on Vivino?" (without one) at the bottom of the detail opens a prefilled issue in the private repo (`meta.issues`, from the state checkout's git remote) in a new tab. The phone remembers the report (localStorage) and shows that wine without its rating at once, with an Undo, until a build lists the pairing in `meta.reported`; from then on the build shows it as reported, and later fixed or checked. Reports reach `state/` on the Mac: `python -m pipeline.run reports` (`pipeline/reports.py`).

Checked in the browser preview at 375 × 812 (27 Sep): store picker (search, remembered store), every filter and sort, detail for rated, review, reject, "too few ratings" and "arriving" wines, a second store, back button closes sheets without moving the list, light and dark. Filtering and sorting 2,231 wines: 0.6–4.4 ms per keystroke including the list redraw; 23 row nodes in the DOM.

Phase 5, checked in the preview at 375 × 812 (28 Sep): each quick pick and clearing it, the value sort, favourites and "Any of my stores" (stock summed, per-store rows in the detail), compare (favourites only: the preview has no location), photos in rows and detail, offline reload with the saved-data note, light and dark.
Phase 6, checked in the preview (28 Sep): "Wrong match?" on a rated wine (the prefilled issue's URL, title and body, which `reports.parse` reads back), the rating hidden in the detail and the list at once, kept across a reload, Undo restoring it; "Know it on Vivino?" on a review-band wine.
