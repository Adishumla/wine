# app

App v1 (phase 3): pick a Systembolaget store, see its wines with Vivino ratings, filter and sort on a phone. One static page, plain JS modules, no build tools.

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
| `app.js` | filters, sorts, store picker, detail, remembered store and filters (localStorage) |
| `style.css` | mobile-first, light and dark |

Data from the build step (`pipeline/build.py`):

| File | Size (27 Sep) | Holds |
|---|---|---|
| `wines.json` | 728 kB | 2,231 wines; countries, regions, grapes etc. as lookup tables; rating, count, adjusted rating and check date only for accept/override |
| `avail/<store>.json` | 5–45 kB each, 454 stores | `[wine index, stock, shelf]`; stock `null` = in the assortment, no stock row yet (upcoming launch, order-only wine not delivered) |
| `stores.json` | 60 kB | id, name (street address when the alias is just the number), town, address, position, wine count, date its list was checked, `stale` (previous list kept) or `partial` (today's list, known incomplete) |
| `meta.json` | – | schema, build time, counts, stock and rating dates, matcher version |

Rules the app keeps:

- Bottle photos come from Systembolaget's image CDN (60 px wide in rows, 200 in the detail): the only thing the app fetches at view time besides its own files. A wine without a photo gets an empty slot. The page sends no referrer.
- A rating shows only for an accepted or overridden match. Review, reject, unmatched and "too few ratings on Vivino" all show "no rating" with the reason in the detail.
- The Vivino link (`vivino.com/w/{id}`) appears only for those same matches.
- Default sort is the adjusted rating (n·r + m·C)/(n + m), C = 3.9, m = 100; ties by rating count, then name.
- Volumes other than 750 ml are shown in the list: half bottles and boxes sit next to bottles.
- Every store lists its full wine assortment. A store whose last fetch didn't add up shows its previous list with a note and that list's date, or, with no previous list, today's with a note that it may be incomplete.
- A wine that hasn't launched yet shows "Arrives <date>", even when bottles are already in the store (they can't be sold before launch day), and doesn't count as in stock.
- A likely match in a group of new wines that isn't hand-checked yet shows "no rating" ("Match not checked yet").

Checked in the browser preview at 375 × 812 (27 Sep): store picker (search, remembered store), every filter and sort, detail for rated, review, reject, "too few ratings" and "arriving" wines, a second store, back button closes sheets without moving the list, light and dark. Filtering and sorting 2,231 wines: 0.6–4.4 ms per keystroke including the list redraw; 23 row nodes in the DOM.
