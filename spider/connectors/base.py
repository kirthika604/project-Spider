"""Connectors are extra trusted sources that feed the same verification chain.

Each returns candidate values with an evidence record (an API record id and
URL instead of a page quote), is cached in SQLite, and is always optional:
a missing key or a service that is down disables that connector only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

import requests

from .. import USER_AGENT
from ..store.db import jdump, now


@dataclass
class ConnectorValue:
    field: str
    value: str
    evidence: str
    url: str = ""
    tier: int = 1
    unit: str | None = None
    kind: str = "value"          # value | alias | identifier | check


@dataclass
class ConnectorResult:
    name: str
    values: list[ConnectorValue] = field(default_factory=list)
    note: str = ""
    ok: bool = True


class Connector:
    name = "connector"
    tier = 1
    uses: tuple[str, ...] = ()

    def __init__(self, conn, config: dict | None = None):
        self.conn = conn
        self.config = config or {}
        self.calls = 0
        self.cache_hits = 0

    # ------------------------------------------------------------- caching
    def cached_get(self, url: str, params: dict | None = None, timeout: int = 15):
        key = f"{self.name}:{url}:{json.dumps(params or {}, sort_keys=True)}"
        row = self.conn.execute("SELECT response FROM ai_cache WHERE key=?",
                                (key[:200],)).fetchone()
        if row:
            self.cache_hits += 1
            try:
                return json.loads(row["response"])
            except ValueError:
                pass
        try:
            response = requests.get(url, params=params, timeout=timeout,
                                    headers={"User-Agent": USER_AGENT,
                                             "Accept": "application/json"})
            self.calls += 1
            if response.status_code != 200:
                return None
            data = response.json()
        except Exception:
            return None
        self.conn.execute(
            "INSERT OR REPLACE INTO ai_cache(key,kind,response,created_at) VALUES(?,?,?,?)",
            (key[:200], f"connector:{self.name}", jdump(data), now()))
        self.conn.commit()
        return data

    # entity types this connector is about; None means "ask the schema"
    entity_types: tuple[str, ...] | None = None

    def applies_to(self, entity_type: str, spec) -> bool:
        """Whether this connector has anything to say about this entity type.

        `for:` in spider.yaml decides it; otherwise the connector's own rule
        does. A species registry should never be asked about a district.
        """
        chosen = self.config.get("for") or self.config.get("entities")
        if chosen:
            chosen = [chosen] if isinstance(chosen, str) else chosen
            return entity_type in chosen
        if self.entity_types is not None:
            return entity_type in self.entity_types
        return True

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        raise NotImplementedError
