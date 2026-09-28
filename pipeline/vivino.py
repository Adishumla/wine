"""Vivino access: Algolia search (index WINES_prod), api.vivino.com/wines/{id} and /vintages/{id}.

vivino.com itself is never contacted. The Algolia app id and public search-only
key come from the environment (VIVINO_ALGOLIA_APP_ID / VIVINO_ALGOLIA_API_KEY)
or, failing that, are read at runtime from bolaget-plus (MIT), a browser
extension that ships the same public credentials. Nothing is committed.
"""

from __future__ import annotations

import json
import os
import re
import time
from urllib.parse import urlencode

from .net import DATA, Blocked, Http

INDEX = "WINES_prod"
BOLAGET_PLUS_SRC = "https://raw.githubusercontent.com/Johanneshn/bolaget-plus/main/src/components/api.ts"
CRED_CACHE = DATA / "cache" / "vivino_algolia.json"


def algolia_credentials(http: Http) -> dict:
    app_id = os.environ.get("VIVINO_ALGOLIA_APP_ID")
    key = os.environ.get("VIVINO_ALGOLIA_API_KEY")
    if app_id and key:
        return {"app_id": app_id, "key": key, "source": "env"}
    if CRED_CACHE.exists():
        return json.loads(CRED_CACHE.read_text())
    src = http.get(BOLAGET_PLUS_SRC, cache=False)
    if not src.ok:
        raise RuntimeError(f"could not read {BOLAGET_PLUS_SRC}: HTTP {src.status}")
    app = re.search(r"VIVINO_ALGOLIA_APP_ID\s*=\s*['\"]([A-Z0-9]{6,20})['\"]", src.text)
    k = re.search(r"VIVINO_ALGOLIA_SEARCH_KEY\s*=\s*['\"]([0-9a-f]{32})['\"]", src.text)
    if not (app and k):
        raise RuntimeError("Algolia credentials not found in bolaget-plus; set VIVINO_ALGOLIA_APP_ID/_API_KEY")
    creds = {"app_id": app.group(1), "key": k.group(1), "source": "bolaget-plus", "read_at": time.time()}
    CRED_CACHE.parent.mkdir(parents=True, exist_ok=True)
    CRED_CACHE.write_text(json.dumps(creds))
    return creds


def search(http: Http, creds: dict, query: str, hits: int = 10, max_age: float | None = None) -> list[dict]:
    """Algolia hits for one query. Cached indefinitely unless max_age says a re-search is due."""
    url = f"https://{creds['app_id'].lower()}-dsn.algolia.net/1/indexes/{INDEX}/query"
    headers = {"X-Algolia-Application-Id": creds["app_id"], "X-Algolia-API-Key": creds["key"]}
    params = {"query": query, "hitsPerPage": hits, "removeWordsIfNoResults": "allOptional"}
    r = http.post_json(url, {"params": urlencode(params)}, headers=headers, max_age=max_age)
    if r.status in (403, 429):
        raise Blocked(f"Algolia HTTP {r.status}")  # stop matching; stored rows stay as they are
    if not r.ok:
        raise RuntimeError(f"Algolia HTTP {r.status}")  # never the body: logs may be public
    return [h for h in r.json().get("hits", []) if not h.get("hidden")]


def wine(http: Http, wine_id: int, max_age: float | None = None) -> tuple[int, dict | None]:
    r = http.get(f"https://api.vivino.com/wines/{wine_id}", max_age=max_age)
    return r.status, r.data()


def wine_of_vintage(http: Http, vintage_id: int) -> int | None:
    """The wine id of a vintage id (vivino.com/wines/{id} links, as Vivino's app shares them)."""
    r = http.get(f"https://api.vivino.com/vintages/{vintage_id}")
    wine_id = ((r.data() or {}).get("wine") or {}).get("id") if r.ok else None
    return int(wine_id) if wine_id else None
