"""Shared HTTP layer for the pipeline (from the phase 1 spike).

Every network request goes through `Http`, which
- enforces the politeness rules from CLAUDE.md per host group
  (Systembolaget: 4 workers, 0.2 s spacing each; Vivino: ~1 req/s shared by
  Algolia and api.vivino.com, backoff on 429/5xx, stop after repeated 403s),
- on a 429, pauses the whole group (Retry-After or longer, doubled per repeat), resumes at
  half the pace and never speeds up past 80 % of the pace that hit the limit; gives up after
  `max_pauses` 429 pauses in a row,
- refuses vivino.com hosts other than api.vivino.com,
- caches 200/404 responses on disk (gzip) so reruns don't repeat requests; `max_age` makes a cached
  response older than that many seconds count as a miss (stock and ratings go stale, search results don't),
- appends one line per network attempt to out/requests.jsonl for the timing report.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(os.environ.get("WINE_DATA") or ROOT / "data")  # gitignored; a symlink to ~/Library/Caches/wine-v2/data
STATE = Path(os.environ.get("WINE_STATE") or ROOT / "state")  # tracked: ids and verdicts only
CACHE = DATA / "cache" / "http"
LOG = DATA / "requests.jsonl"

USER_AGENT = "wine-v2/0.2 (private personal tool; low volume; python-httpx)"
TRANSPORT: httpx.BaseTransport | None = None  # tests swap in an httpx.MockTransport


@dataclass(frozen=True)
class Policy:
    group: str
    workers: int  # max concurrent requests in the group
    spacing: float  # seconds a worker waits after each request
    min_interval: float  # min seconds between request starts across the group (the fastest pace)
    retries: int  # retries on network errors and 5xx
    backoff: float  # first backoff in seconds, doubled per retry
    max_403: int  # consecutive 403s before the group is declared blocked
    start_interval: float | None = None  # starting pace if slower than min_interval
    cooldown: float = 60.0  # first group pause on a 429 (or Retry-After if longer), doubled per repeat
    max_pauses: int = 5  # 429 pauses in a row (no success in between) before the group gives up
    max_interval: float = 10.0  # slowest pace the halving goes down to
    speedup_after: int = 0  # successes in a row before the pace speeds up 20 % (0 = never)


# Systembolaget's API allowed ~43 calls in 35 s, then 429 for minutes: start at 1 req/s and adapt.
SB = Policy("systembolaget", workers=4, spacing=0.2, min_interval=0.5, retries=3, backoff=1.0, max_403=3,
            start_interval=1.0, cooldown=60.0, max_pauses=5, speedup_after=50)
VIVINO = Policy("vivino", workers=1, spacing=0.0, min_interval=1.0, retries=4, backoff=2.0, max_403=3,
                cooldown=10.0, max_pauses=5)
OTHER = Policy("other", workers=2, spacing=0.3, min_interval=0.0, retries=2, backoff=1.0, max_403=3,
               cooldown=10.0, max_pauses=3)
MAX_PAUSE = 15 * 60.0
RATE_HEADERS = ("retry-after", "ratelimit", "rate-limit", "x-rate", "quota")


def policy_for(host: str) -> Policy:
    if host.endswith("systembolaget.se"):
        return SB
    if host == "api.vivino.com" or host.endswith(".algolia.net") or host.endswith(".algolianet.com"):
        return VIVINO
    if host.endswith("vivino.com"):
        raise ValueError(f"refusing {host}: only api.vivino.com and Algolia are allowed")
    return OTHER


class Offline(RuntimeError):
    """A cache miss in an offline Http (the build step must not touch the network)."""


class Blocked(RuntimeError):
    """Raised when a host group returns repeated 403s (or any 403/429 in stop-on-first mode)."""


@dataclass
class Resp:
    url: str
    status: int
    text: str
    elapsed_ms: float | None
    from_cache: bool
    content: bytes = b""  # the raw body, for download() only

    def json(self) -> Any:
        return json.loads(self.text)

    def data(self) -> Any:
        """Parsed JSON body of a 200 response, else None."""
        if self.status != 200 or not self.text.strip():
            return None
        try:
            return json.loads(self.text)
        except ValueError:
            return None

    @property
    def ok(self) -> bool:
        return self.status == 200


class _Group:
    """Pace and 429 state of one host group, shared by every Http in the process."""

    def __init__(self, policy: Policy):
        self.policy = policy
        self.sem = threading.Semaphore(policy.workers)
        self.lock = threading.Lock()
        self.next_start = 0.0
        self.interval = max(policy.min_interval, policy.start_interval or 0.0)
        self.fastest = policy.min_interval  # raised to 1.25 × the pace of each 429
        self.paused_until = 0.0
        self.pause_epoch = 0  # bumped per pause, so 429s of requests already in flight don't stack
        self.pauses = 0  # pauses since the last success
        self.streak = 0
        self.consecutive_403 = 0
        self.blocked = False
        self.stats = {"requests": 0, "429": 0, "pauses": 0, "paused_s": 0.0}

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.stats, "paused_s": round(self.stats["paused_s"], 1),
                    "rate_now": round(1 / self.interval, 3) if self.interval else None,
                    "rate_cap": round(1 / self.fastest, 3) if self.fastest else None, "blocked": self.blocked}


_GROUPS: dict[str, _Group] = {}
_GLOCK = threading.Lock()


def _group(policy: Policy) -> _Group:
    with _GLOCK:
        g = _GROUPS.get(policy.group)
        if g is None or g.policy is not policy:
            g = _GROUPS[policy.group] = _Group(policy)
        return g


def group_stats() -> dict[str, dict]:
    """Requests, 429s, pauses and the current pace per group, for run_status.json."""
    with _GLOCK:
        groups = dict(_GROUPS)
    return {name: g.snapshot() for name, g in groups.items()}


def reset_groups() -> None:
    with _GLOCK:
        _GROUPS.clear()


def _retry_after(value: str | None) -> float | None:
    """Retry-After in seconds: delta-seconds or an HTTP date."""
    if not value:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class Http:
    def __init__(self, script: str, stop_on_first_block: bool = False, use_cache: bool = True, offline: bool = False):
        self.script = script
        self.offline = offline
        self.stop_on_first_block = stop_on_first_block
        self.use_cache = use_cache
        self.client = httpx.Client(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json, text/plain, */*"},
            timeout=httpx.Timeout(30.0, connect=10.0),
            follow_redirects=True,
            transport=TRANSPORT,
        )
        self._log_lock = threading.Lock()
        DATA.mkdir(parents=True, exist_ok=True)

    # -- cache ---------------------------------------------------------------

    @staticmethod
    def _full_url(url: str, params: dict | None) -> str:
        if not params:
            return url
        return url + ("&" if "?" in url else "?") + urlencode(sorted(params.items()), doseq=True)

    def _cache_path(self, group: str, method: str, full_url: str, body: Any) -> Path:
        raw = json.dumps([method, full_url, body], sort_keys=True, ensure_ascii=False)
        return CACHE / group / f"{hashlib.sha256(raw.encode()).hexdigest()[:32]}.json.gz"

    def _log(self, **rec: Any) -> None:
        rec = {"ts": round(time.time(), 3), "script": self.script, **rec}
        with self._log_lock, LOG.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    # -- pacing ---------------------------------------------------------------

    @staticmethod
    def _wait_turn(group: _Group) -> int:
        """Sleep until the group's pace and any 429 pause allow a start; return the pause epoch."""
        while True:
            with group.lock:
                if group.blocked:
                    raise Blocked(f"{group.policy.group} gave up after repeated 429s or 403s")
                now = time.monotonic()
                start = max(group.next_start, group.paused_until)
                if start <= now:
                    group.next_start = now + group.interval
                    return group.pause_epoch
            time.sleep(min(start - now, 1.0))  # short naps, so a pause started meanwhile is seen

    def _on_429(self, group: _Group, epoch: int, host: str, headers: dict) -> None:
        p = group.policy
        with group.lock:
            group.stats["429"] += 1
            if self.stop_on_first_block:
                group.blocked = True
                raise Blocked(f"{p.group}: 429 from {host}")
            if epoch != group.pause_epoch:
                return  # sent before the current pause started: just wait it out
            group.pause_epoch += 1
            group.pauses += 1
            if group.pauses > p.max_pauses:
                group.blocked = True
                raise Blocked(f"{p.group}: still 429 after {p.max_pauses} pauses")
            server = _retry_after(next((v for k, v in headers.items() if k.lower() == "retry-after"), None))
            pause = max(min(p.cooldown * 2 ** (group.pauses - 1), MAX_PAUSE), server or 0.0)
            group.paused_until = time.monotonic() + pause
            if group.pauses == 1:  # first 429 since a success: our pace was too high. Later ones: window not over.
                group.fastest = min(max(group.fastest, group.interval * 1.25), p.max_interval)
                group.interval = max(min(group.interval * 2, p.max_interval), group.fastest)
            group.streak = 0
            group.stats["pauses"] += 1
            group.stats["paused_s"] += pause
            n, rate = group.pauses, (1 / group.interval if group.interval else float("inf"))
        self._log(group=p.group, host=host, event="pause", seconds=round(pause, 1), retry_after=server,
                  pause_no=n, rate_after=round(rate, 3))
        print(f"[net] {p.group}: 429, pausing {pause:.0f} s (#{n}), then {rate:.2f} req/s", flush=True)

    # -- requests ------------------------------------------------------------

    def get(self, url: str, params: dict | None = None, headers: dict | None = None, cache: bool = True,
            max_age: float | None = None) -> Resp:
        return self.request("GET", url, params=params, headers=headers, cache=cache, max_age=max_age)

    def download(self, url: str) -> Resp:
        """A binary GET (a product photo), paced like any request to its host; never cached as JSON."""
        return self.request("GET", url, cache=False, binary=True)

    def post_json(self, url: str, body: Any, headers: dict | None = None, cache: bool = True,
                  max_age: float | None = None) -> Resp:
        return self.request("POST", url, body=body, headers=headers, cache=cache, max_age=max_age)

    def request(
        self,
        method: str,
        url: str,
        params: dict | None = None,
        body: Any = None,
        headers: dict | None = None,
        cache: bool = True,
        max_age: float | None = None,
        binary: bool = False,
    ) -> Resp:
        host = urlsplit(url).hostname or ""
        policy = policy_for(host)
        group = _group(policy)
        full_url = self._full_url(url, params)
        cpath = self._cache_path(policy.group, method, full_url, body)

        if cache and self.use_cache and cpath.exists():
            try:
                with gzip.open(cpath, "rt") as f:
                    c = json.load(f)
            except (OSError, EOFError, ValueError):
                c = None  # unreadable (a run killed mid-write before writes were atomic): a miss
            if c and (max_age is None or time.time() - c.get("fetched_at", 0) <= max_age):
                return Resp(full_url, c["status"], c["text"], c.get("elapsed_ms"), True)

        if self.offline:
            raise Offline(f"not cached: {method} {host}")  # no path or query: they can hold ids, logs may be public
        if group.blocked:
            raise Blocked(f"{policy.group} is blocked; not sending {method} to {host}")

        split = urlsplit(full_url)
        log_path = split.path + (f"?{split.query}" if split.query else "")
        attempt = 0
        while True:
            with group.sem:
                epoch = self._wait_turn(group)
                t0 = time.perf_counter()
                err = None
                status = 0
                text = ""
                content = b""
                extra: dict = {}
                try:
                    r = self.client.request(method, full_url, json=body, headers=headers)
                    status = r.status_code
                    if binary:
                        content = r.content
                    else:
                        text = r.text
                    if status == 429:
                        # Everything the server says about its limit, so the report can state it.
                        extra = {"body": text[:200], "headers": {k: v for k, v in r.headers.items()
                                                                 if any(h in k.lower() for h in RATE_HEADERS)}}
                except httpx.HTTPError as e:
                    err = f"{type(e).__name__}: {e}"
                ms = (time.perf_counter() - t0) * 1000
                with group.lock:
                    group.stats["requests"] += 1
                self._log(group=policy.group, host=host, method=method, path=log_path[:300],
                          status=status, ms=round(ms, 1), attempt=attempt, error=err, **extra)
                if policy.spacing:
                    time.sleep(policy.spacing)

            if status == 403:
                with group.lock:
                    group.consecutive_403 += 1
                    if self.stop_on_first_block or group.consecutive_403 >= policy.max_403:
                        group.blocked = True
                if group.blocked:
                    raise Blocked(f"{policy.group}: 403 from {host} ({group.consecutive_403} in a row)")
                return Resp(full_url, status, text, ms, False)

            if status == 429:
                self._on_429(group, epoch, host, extra.get("headers", {}))
                attempt += 1
                continue

            if err is not None or status >= 500:
                if attempt < policy.retries:
                    time.sleep(policy.backoff * (2**attempt))
                    attempt += 1
                    continue
                if err is not None:
                    raise httpx.HTTPError(err)
                return Resp(full_url, status, text, ms, False)

            with group.lock:  # the limiter let us through
                group.pauses = 0
                if 200 <= status < 300:
                    group.consecutive_403 = 0
                if policy.speedup_after:
                    group.streak += 1
                    if group.streak >= policy.speedup_after:
                        group.streak = 0
                        group.interval = max(group.interval / 1.2, group.fastest)

            if cache and self.use_cache and status in (200, 404):
                cpath.parent.mkdir(parents=True, exist_ok=True)
                tmp = cpath.with_name(f"{cpath.name}.{threading.get_ident()}.tmp")
                with gzip.open(tmp, "wt") as f:
                    json.dump({"url": full_url, "status": status, "text": text, "elapsed_ms": round(ms, 1),
                               "fetched_at": time.time()}, f)
                tmp.replace(cpath)  # a killed run never leaves a half-written entry
            return Resp(full_url, status, text, ms, False, content)


def save_json(name: str, data: Any) -> Path:
    path = DATA / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return path


def load_json(name: str) -> Any:
    return json.loads((DATA / name).read_text())
