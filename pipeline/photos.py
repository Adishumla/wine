"""Phase 5: bottle photos for the app, copied onto the private site, so the phone never asks Systembolaget for them
and they work offline.

For every wine in the catalog (orders.py: the stores' wines and the order-only range) without a photo yet, the 60 px wide WebP from Systembolaget's image CDN
(`product-cdn.systembolaget.se/productimages/{id}/{id}_60.webp`, 3-7 KB), paced like every Systembolaget request
(at most 2 req/s shared). At most `limit` downloads a run, widely carried wines first and order-only wines last:
the first nights fill in the range, after that only new wines. A 404 (no photo) is remembered and tried again after
RETRY_DAYS. Photos of wines that left the catalog are removed.

Writes data/app/img/{productId}.webp (deployed with the app) and data/photos.json (the 404s).
"""

from __future__ import annotations

import datetime
import time

import httpx

from .net import DATA, Blocked, Http, load_json, save_json
from .orders import catalog

IMG = "https://product-cdn.systembolaget.se/productimages/{id}/{id}_60.webp"
DIR = DATA / "app" / "img"
MISSING = "photos.json"
RETRY_DAYS = 30


def run(http: Http, limit: int = 1500, today: datetime.date | None = None) -> dict:
    t0 = time.time()
    today = today or datetime.date.today()
    wines = catalog()
    DIR.mkdir(parents=True, exist_ok=True)
    missing: dict = load_json(MISSING) if (DATA / MISSING).exists() else {}
    removed = 0
    for f in DIR.glob("*.webp"):
        if f.stem not in wines:
            f.unlink()
            removed += 1
    have = {f.stem for f in DIR.glob("*.webp")}

    def recent_404(pid: str) -> bool:
        tried = (missing.get(pid) or {}).get("tried_at")
        return bool(tried) and (today - datetime.date.fromisoformat(tried)).days < RETRY_DAYS

    todo = [pid for pid in sorted(wines, key=lambda pid: (-(wines[pid].get("ns") or 0), pid))
            if pid not in have and not recent_404(pid)]
    stats = {"wines": len(wines), "had": len(have), "removed": removed, "downloaded": 0, "no_photo": 0, "failed": 0}
    done = 0
    try:
        for pid in todo[:limit]:
            done += 1
            try:
                r = http.download(IMG.format(id=pid))
            except httpx.HTTPError:
                stats["failed"] += 1  # a network error after Http's retries: next run
                continue
            if r.status == 200 and r.content[:4] == b"RIFF" and r.content[8:12] == b"WEBP":
                tmp = DIR / f"{pid}.webp.tmp"
                tmp.write_bytes(r.content)
                tmp.replace(DIR / f"{pid}.webp")
                missing.pop(pid, None)
                stats["downloaded"] += 1
            elif r.status == 404:
                missing[pid] = {"status": 404, "tried_at": today.isoformat()}
                stats["no_photo"] += 1
            else:
                stats["failed"] += 1
    except Blocked as e:
        stats["blocked"] = str(e)
    save_json(MISSING, {pid: v for pid, v in missing.items() if pid in wines})
    stats |= {"have": len(have) + stats["downloaded"], "left": len(todo) - done, "seconds": round(time.time() - t0, 1)}
    save_json("photos_run.json", stats)
    return stats
