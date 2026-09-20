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


def domain_of(url: str) -> str:
    host = (urlparse(url).netloc or "").lower()
    return host[4:] if host.startswith("www.") else host


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
        self.allowed = {d.lower().lstrip("www.") for d in (allowed_domains or [])}
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
        if not self.any_domain and domain_of(url) not in self.allowed:
            return False
        self.seen.add(url)
        self.queue.append((url, depth, self.tier_of(url)))
        return True

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
