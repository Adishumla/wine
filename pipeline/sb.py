"""Systembolaget ecommerce API: the unofficial endpoints systembolaget.se itself calls.

Route map measured without a key (APIM answers 404 for unknown routes before it
checks the key, 401 for real ones): productsearch/search exists under v1 and v2;
site/stores, site/stores/{id} and stockbalance/store/{store}/{product} only under v1.
"""

from __future__ import annotations

import json
import math
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import httpx

from .net import DATA, Http, Resp

BASE = "https://api-extern.systembolaget.se/sb-api-ecommerce"
SITE = "https://www.systembolaget.se"
KEY_CACHE = DATA / "cache" / "sb_key.json"
# Matches `NEXT_PUBLIC_API_KEY_APIM:"…"`, `"NEXT_PUBLIC_API_KEY_APIM":"…"` and the escaped form inside RSC payloads.
KEY_RE = re.compile(r"""NEXT_PUBLIC_API_KEY_APIM\\?["']?\s*[:=]\s*\\?["']([A-Za-z0-9]{16,64})\\?["']""")
SCRIPT_RE = re.compile(r"""["'](/_next/static/[^"']+?\.js)["']""")
# Sorts with few ties, tried in turn until a slice's unique products reach docCount (probe_pagination.py:
# default sort 330/366 unique, vintage 350, price 362, name and launch date 366; on 4,903 wines launch date
# alone reached 4,883). Unknown sortBy values are silently ignored, so there's no unique-key sort.
SORT_PASSES = (("Name", "Ascending"), ("ProductLaunchDate", "Descending"), ("Price", "Ascending"))


def extract_key(http: Http) -> tuple[str, dict]:
    """Find the APIM subscription key in systembolaget.se's HTML or JS chunks."""
    t0 = time.time()
    home = http.get(SITE + "/", cache=False)
    if not home.ok:
        raise RuntimeError(f"systembolaget.se returned HTTP {home.status}")
    m = KEY_RE.search(home.text)
    if m:
        return m.group(1), {"found_in": "html", "chunks_scanned": 0, "seconds": round(time.time() - t0, 1)}
    scripts = list(dict.fromkeys(SCRIPT_RE.findall(home.text)))
    for i, path in enumerate(scripts, 1):
        js = http.get(SITE + path)  # content-hashed names, safe to cache
        m = KEY_RE.search(js.text) if js.ok else None
        if m:
            return m.group(1), {"found_in": path, "chunks_scanned": i, "chunks_total": len(scripts),
                                "seconds": round(time.time() - t0, 1)}
    raise RuntimeError(f"NEXT_PUBLIC_API_KEY_APIM not found in HTML or {len(scripts)} chunks")


class SB:
    def __init__(self, http: Http):
        self.http = http
        self._key: str | None = None
        self.key_info: dict = {}
        self._lock = threading.Lock()

    def key(self, refresh: bool = False) -> str:
        with self._lock:
            if self._key and not refresh:
                return self._key
            env = os.environ.get("SB_API_KEY")
            if env and not refresh:
                self._key, self.key_info = env, {"found_in": "env"}
            elif KEY_CACHE.exists() and not refresh:
                c = json.loads(KEY_CACHE.read_text())
                self._key, self.key_info = c["key"], {**c["info"], "cached": True}
            else:
                self._key, self.key_info = extract_key(self.http)
                KEY_CACHE.parent.mkdir(parents=True, exist_ok=True)
                KEY_CACHE.write_text(json.dumps({"key": self._key, "info": self.key_info, "at": time.time()}))
            return self._key

    def get(self, path: str, params: dict | None = None, version: str = "v1", cache: bool = True,
            max_age: float | None = None) -> Resp:
        url = f"{BASE}/{version}/{path.lstrip('/')}"
        try:
            r = self.http.get(url, params=params, headers={"Ocp-Apim-Subscription-Key": self.key()}, cache=cache,
                              max_age=max_age)
            if r.status == 401 and self.key_info.get("found_in") != "env":
                # Key rotated since it was cached: extract again once.
                self.key(refresh=True)
                r = self.http.get(url, params=params, headers={"Ocp-Apim-Subscription-Key": self.key()},
                                  cache=cache, max_age=max_age)
        except httpx.HTTPError:
            # A network error that outlived Http's retries: a failed request (status 0) the callers already handle
            # (a retry round, a failed page, a stale store), not an exception that ends an hour-long step.
            return Resp(url, 0, "", None, False)
        return r

    # -- product search ------------------------------------------------------

    def search(self, params: dict, page: int = 1, size: int = 30, version: str = "v2",
               max_age: float | None = None) -> tuple[Resp, dict]:
        r = self.get("productsearch/search", {**params, "page": page, "size": size}, version=version, max_age=max_age)
        return r, (r.data() or {})

    def _pages(self, params: dict, size: int, version: str, max_pages: int | None,
               max_age: float | None = None) -> tuple[dict, dict, dict]:
        """Every page of one search, in parallel: (pages, statuses, first page's metadata)."""
        r, first = self.search(params, 1, size, version, max_age)
        pages: dict[int, dict] = {1: first}
        statuses: dict[int, int] = {1: r.status}
        if not r.ok:
            return pages, statuses, {}
        meta = first.get("metadata") or {}
        doc = meta.get("docCount")
        n_pages = math.ceil(doc / size) if isinstance(doc, int) else 1
        if max_pages:
            n_pages = min(n_pages, max_pages)

        def fetch(p: int) -> tuple[int, int, dict]:
            rr, d = self.search(params, p, size, version, max_age)
            return p, rr.status, d

        with ThreadPoolExecutor(max_workers=4) as ex:
            for p, st, d in ex.map(fetch, range(2, n_pages + 1)):
                pages[p], statuses[p] = d, st
        return pages, statuses, meta

    def search_all(self, params: dict, size: int = 30, version: str = "v2", max_pages: int | None = None,
                   sorts: tuple[tuple[str, str], ...] = SORT_PASSES, max_age: float | None = None) -> dict:
        """Fetch every page of one search. Checks nextPage == -1 and the repeat-last-page cap.

        The API pages unstably when sort keys tie (the default relevance score ties for every product in
        a filter-only search), so pages repeat some products and skip others. Pass 1 sorts by name; while
        unique products fall short of docCount, another pass with a different sort is added."""
        products: dict[str, dict] = {}
        passes: list[dict] = []
        total_pages = repeats = 0
        failed: list[str] = []
        meta: dict = {}
        first_page: dict = {}
        last_meta: dict = {}
        for by, direction in sorts:
            pages, statuses, m = self._pages({**params, "sortBy": by, "sortDirection": direction}, size, version,
                                             max_pages, max_age)
            if not passes:
                meta, first_page = m, pages.get(1, {})
            total_pages += len(pages)
            failed += [f"{by}:{p}" for p, st in sorted(statuses.items()) if st != 200]
            prev_ids: list[str] | None = None
            for p in sorted(pages):
                ids = [str(x.get("productId")) for x in pages[p].get("products") or []]
                if prev_ids is not None and ids and ids == prev_ids:
                    repeats += 1
                prev_ids = ids
                for x in pages[p].get("products") or []:
                    products.setdefault(str(x.get("productId")), x)
            last_meta = (pages[max(pages)].get("metadata") or {}) if pages else {}
            passes.append({"sort": f"{by} {direction}", "pages": len(pages), "unique_so_far": len(products)})
            doc = meta.get("docCount")
            if failed or max_pages or not isinstance(doc, int) or len(products) >= doc:
                break
        doc = meta.get("docCount")
        status = 200 if not failed else 0
        if not meta:
            status = next(iter(statuses.values()), 0) if passes else 0
        return {
            # 200 only when every page came back; complete only when unique products also equal docCount.
            "status": status,
            "complete": not failed and isinstance(doc, int) and len(products) == doc,
            "doc_count": doc,
            "pages": total_pages,
            "passes": passes,
            "failed_pages": failed,
            "products": list(products.values()),
            "unique": len(products),
            "repeated_pages": repeats,
            "last_next_page": last_meta.get("nextPage"),
            "first_metadata": {k: v for k, v in meta.items() if not isinstance(v, (dict, list))},
            "filter_names": [f.get("name") for f in first_page.get("filters") or [] if isinstance(f, dict)],
        }


def store_ids_in(data: Any) -> list[str]:
    """Pull store ids out of an unknown JSON shape (list of stores, or wrapped)."""
    out: list[str] = []

    def walk(o: Any) -> None:
        if isinstance(o, dict):
            sid = o.get("siteId") or o.get("storeId")
            if sid is not None:
                out.append(str(sid))
                return
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return list(dict.fromkeys(out))


def shape(o: Any, depth: int = 0) -> Any:
    """Type skeleton of a JSON value, for recording unknown response formats in the report."""
    if depth > 3:
        return type(o).__name__
    if isinstance(o, dict):
        return {k: shape(v, depth + 1) for k, v in list(o.items())[:40]}
    if isinstance(o, list):
        return [f"{len(o)} items", shape(o[0], depth + 1)] if o else []
    return type(o).__name__
