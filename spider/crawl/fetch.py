"""HTTP fetching: HTML only, 15 second timeout, never raises (NFR-4)."""

from __future__ import annotations

from dataclasses import dataclass

import re

import requests

from .. import USER_AGENT

META_CHARSET = re.compile(
    rb"""<meta[^>]+charset=["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)

TIMEOUT = 15
MAX_BYTES = 5_000_000


@dataclass
class Fetched:
    url: str
    status: int
    html: str = ""
    content_type: str = ""
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and "html" in self.content_type and bool(self.html)


_session = requests.Session()
_session.headers.update({
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en,hi;q=0.8,ta;q=0.6",
})


def _decode(body: bytes, content_type: str) -> str:
    """Decode HTML, trusting the page's own charset when the header omits it.

    requests falls back to ISO-8859-1 for text/* without a charset, which
    turns Hindi and Tamil pages into mojibake.
    """
    if "charset=" in content_type:
        declared = content_type.split("charset=", 1)[1].split(";")[0].strip(" \"'")
        try:
            return body.decode(declared, errors="replace")
        except LookupError:
            pass
    found = META_CHARSET.search(body[:4096])
    if found:
        try:
            return body.decode(found.group(1).decode("ascii"), errors="replace")
        except (LookupError, UnicodeDecodeError):
            pass
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError:
        return body.decode("cp1252", errors="replace")


def fetch(url: str, timeout: int = TIMEOUT) -> Fetched:
    try:
        resp = _session.get(url, timeout=timeout, allow_redirects=True, stream=True)
        ctype = resp.headers.get("Content-Type", "").lower()
        if "html" not in ctype:
            resp.close()
            return Fetched(url, resp.status_code, content_type=ctype,
                           error=f"not HTML ({ctype or 'unknown type'})")
        body = resp.raw.read(MAX_BYTES, decode_content=True) or b""
        resp.close()
        return Fetched(url, resp.status_code, _decode(body, ctype), ctype)
    except requests.RequestException as exc:
        return Fetched(url, 0, error=str(exc)[:200])
    except Exception as exc:                       # a bad page never stops a crawl
        return Fetched(url, 0, error=f"{type(exc).__name__}: {exc}"[:200])
