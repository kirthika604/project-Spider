"""Breadth-first URL queue with depth and domain rules (FR-2, FR-3, FR-5)."""

from __future__ import annotations

from collections import deque
from urllib.parse import urldefrag, urljoin, urlparse

SKIP_EXTENSIONS = (
    ".jpg", ".jpeg", ".png", ".gif", ".svg", ".webp", ".ico", ".css", ".js",
    ".zip", ".gz", ".tar", ".mp4", ".mp3", ".avi", ".doc", ".docx", ".ppt",
    ".pptx", ".xls", ".xlsx", ".exe", ".dmg",
)


def normalise(url: str) -> str:
    url, _ = urldefrag(url.strip())
    if url.endswith("/") and urlparse(url).path != "/":
        url = url[:-1]
    return url


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def domain_of(url: str) -> str:
    return _strip_www((urlparse(url).netloc or "").lower())


def is_crawlable(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    return not parsed.path.lower().endswith(SKIP_EXTENSIONS)


class Frontier:
    """FIFO queue of (url, depth, tier) that applies the domain policy."""

    def __init__(self, seeds, max_depth: int = 2, any_domain: bool = False,
                 allowed_domains=None, tier_of=None):
        self.max_depth = max_depth
        self.any_domain = any_domain
        self.tier_of = tier_of or (lambda _url: 3)
        self.allowed = {_strip_www(d.lower()) for d in (allowed_domains or [])}
        self.seen: set[str] = set()
        self.queue: deque = deque()
        for seed in seeds:
            seed = normalise(seed)
            if not self.any_domain:
                self.allowed.add(domain_of(seed))
            self.push(seed, 0)

    def push(self, url: str, depth: int) -> bool:
        url = normalise(url)
        if url in self.seen or depth > self.max_depth or not is_crawlable(url):
            return False
        if not self.any_domain and not self._permitted(url):
            return False
        self.seen.add(url)
        self.queue.append((url, depth, self.tier_of(url)))
        return True

    def _permitted(self, url: str) -> bool:
        """Whether a URL is on a domain the crawl may visit.

        A domain in the list also covers its subdomains (`example.org` allows
        `www.example.org` and `data.example.org`), and a domain given without a
        port matches the same host on any port. `lstrip("www.")` - which strips
        any leading run of w and . characters - was used here before, and
        turned `webmd.com` into `ebmd.com`.
        """
        netloc = domain_of(url)
        host = netloc.rsplit(":", 1)[0] if netloc.count(":") == 1 else netloc
        for allowed in self.allowed:
            bare = allowed.rsplit(":", 1)[0] if allowed.count(":") == 1 and \
                not allowed.rsplit(":", 1)[1].isalpha() else allowed
            if netloc == allowed or host == allowed or host == bare \
                    or host.endswith("." + bare):
                return True
        return False

    def add_links(self, base_url: str, links, depth: int) -> int:
        added = 0
        for link in links:
            try:
                absolute = urljoin(base_url, link)
            except ValueError:
                continue
            if self.push(absolute, depth + 1):
                added += 1
        return added

    def pop(self):
        return self.queue.popleft() if self.queue else None

    def __len__(self) -> int:
        return len(self.queue)
