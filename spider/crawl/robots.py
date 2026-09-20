"""robots.txt compliance and per-domain rate limiting (NFR-1)."""

from __future__ import annotations

import threading
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse

import requests

from .. import USER_AGENT


class Politeness:
    """Caches robots.txt per domain and enforces the crawl delay."""

    def __init__(self, default_delay: float = 1.0, respect_robots: bool = True):
        self.default_delay = default_delay
        self.respect_robots = respect_robots
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_hit: dict[str, float] = {}
        self._lock = threading.Lock()          # guards the two dicts only
        self._host_locks: dict[str, threading.Lock] = {}
        self.blocked: dict[str, str] = {}   # domain -> reason, shown in reports

    def _parser(self, url: str):
        host = urlparse(url).netloc
        if host in self._robots:
            return self._robots[host]
        parser = urllib.robotparser.RobotFileParser()
        robots_url = urljoin(f"{urlparse(url).scheme}://{host}", "/robots.txt")
        try:
            resp = requests.get(robots_url, timeout=10,
                                headers={"User-Agent": USER_AGENT})
            if resp.status_code == 200:
                parser.parse(resp.text.splitlines())
            else:
                parser.parse([])           # no robots.txt means everything allowed
        except requests.RequestException:
            parser.parse([])
        self._robots[host] = parser
        return parser

    def allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        try:
            ok = self._parser(url).can_fetch(USER_AGENT, url)
        except Exception:
            return True
        if not ok:
            self.blocked.setdefault(urlparse(url).netloc, "disallowed by robots.txt")
        return ok

    def delay_for(self, url: str) -> float:
        parser = self._parser(url) if self.respect_robots else None
        site_delay = None
        if parser is not None:
            try:
                site_delay = parser.crawl_delay(USER_AGENT)
            except Exception:
                site_delay = None
        return max(self.default_delay, float(site_delay or 0))

    def _host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            return self._host_locks.setdefault(host, threading.Lock())

    def wait(self, url: str) -> None:
        """Sleep until this domain's delay has passed since the last request.

        The wait is per domain, not global: waiting a second for one site must
        not hold up a request to another, or a crawl over ten sites runs ten
        times slower than the politeness rule actually requires.
        """
        host = urlparse(url).netloc
        delay = self.delay_for(url)
        with self._host_lock(host):
            last = self._last_hit.get(host, 0.0)
            gap = time.time() - last
            if gap < delay:
                time.sleep(delay - gap)
            with self._lock:
                self._last_hit[host] = time.time()
