"""429 handling in pipeline/net.py against a mock server. No network. Run: python -m pipeline.tests.test_net"""

from __future__ import annotations

import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor

os.environ["WINE_DATA"] = tempfile.mkdtemp(prefix="pipeline-net-")

import httpx  # noqa: E402

from pipeline import net  # noqa: E402

URL = "https://api-extern.systembolaget.se/x"


class Server:
    """Answers 429 while `limited` is set; records when each request arrived."""

    def __init__(self, retry_after: str | None = None):
        self.limited = False
        self.retry_after = retry_after
        self.hits: list[tuple[float, int]] = []
        self.lock = threading.Lock()

    def __call__(self, req: httpx.Request) -> httpx.Response:
        status = 429 if self.limited else 200
        with self.lock:
            self.hits.append((time.monotonic(), status))
        headers = {"Retry-After": self.retry_after} if status == 429 and self.retry_after else {}
        return httpx.Response(status, headers=headers, json={"statusCode": status, "message": "Rate limit"})


def setup(server: Server, **policy) -> net.Http:
    net.reset_groups()
    net.TRANSPORT = httpx.MockTransport(server)
    net.SB = net.Policy("systembolaget", **{"workers": 4, "spacing": 0, "min_interval": 0.01, "retries": 0,
                                            "backoff": 0.01, "max_403": 3, "cooldown": 0.3, **policy})
    return net.Http("test", use_cache=False)


def test_one_pause_for_concurrent_429s_and_no_requests_during_it() -> None:
    server = Server()
    http = setup(server)
    server.limited = True
    threading.Timer(0.1, lambda: setattr(server, "limited", False)).start()
    with ThreadPoolExecutor(4) as ex:
        assert all(r.ok for r in ex.map(lambda _: http.get(URL), range(8)))
    s = net.group_stats()["systembolaget"]
    assert s["pauses"] == 1, s  # four workers hit 429 together: one pause, not four
    last_429 = max(t for t, st in server.hits if st == 429)
    first_ok = min(t for t, st in server.hits if st == 200)
    assert first_ok - last_429 >= 0.25, first_ok - last_429  # nobody poked the API during the pause
    assert abs(s["rate_now"] - 50) < 1 and abs(s["rate_cap"] - 80) < 1, s  # 100 → 50 req/s, cap 80 %


def test_retry_after_is_honoured() -> None:
    server = Server(retry_after="1")
    http = setup(server)
    server.limited = True
    threading.Timer(0.1, lambda: setattr(server, "limited", False)).start()
    t0 = time.monotonic()
    assert http.get(URL).ok
    assert time.monotonic() - t0 >= 1.0


def test_gives_up_after_max_pauses() -> None:
    server = Server()
    http = setup(server, cooldown=0.01, max_pauses=3)
    server.limited = True
    try:
        http.get(URL)
    except net.Blocked:
        pass
    else:
        raise AssertionError("expected Blocked")
    assert len(server.hits) == 4, server.hits  # first call + one per pause
    try:
        http.get(URL)
    except net.Blocked:
        pass
    else:
        raise AssertionError("group should stay blocked")
    assert len(server.hits) == 4


def test_speeds_up_but_not_past_the_cap() -> None:
    server = Server()
    http = setup(server, min_interval=0.001, start_interval=0.01, speedup_after=5, cooldown=0.01)
    server.limited = True
    threading.Timer(0.02, lambda: setattr(server, "limited", False)).start()
    for _ in range(200):
        assert http.get(URL).ok
    s = net.group_stats()["systembolaget"]
    assert s["rate_now"] == s["rate_cap"] == 80.0, s  # climbed back from 50 but stopped at 80 % of 100


def test_stop_on_first_block() -> None:
    server = Server()
    setup(server)
    http = net.Http("ci", use_cache=False, stop_on_first_block=True)
    server.limited = True
    try:
        http.get(URL)
    except net.Blocked:
        pass
    else:
        raise AssertionError("expected Blocked")
    assert len(server.hits) == 1


if __name__ == "__main__":
    tests = [v for k, v in dict(globals()).items() if k.startswith("test_")]
    for t in tests:
        t()
        print(f"ok {t.__name__}")
    print(f"{len(tests)} tests passed")
