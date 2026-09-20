"""Coordinates for a place name, from OpenStreetMap's Nominatim.

This is the connector to reach for when your records are places and you need
latitude and longitude: you crawl or list the names, and this fills the
coordinates, the official name and the OSM id.

Nominatim is a free service with a usage policy: at most one request a
second, and a real User-Agent. Both are honoured here, and every answer is
cached in the project, so a second build asks for nothing.
"""

from __future__ import annotations

import time

from .base import Connector, ConnectorResult, ConnectorValue

SEARCH = "https://nominatim.openstreetmap.org/search"


class GeocodeConnector(Connector):
    name = "geocode"
    tier = 1
    uses = ("latitude", "longitude", "official_name", "identifier")
    min_interval = 1.1                      # Nominatim's published limit
    _last_call = 0.0

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        region = (self.config.get("within") or self.config.get("region") or "").strip()
        query = f"{name}, {region}" if region and region.lower() not in name.lower() \
            else name
        params = {"q": query, "format": "json", "limit": 1, "addressdetails": 1}
        if self.config.get("country"):
            params["countrycodes"] = str(self.config["country"]).lower()

        cache_key = f"{SEARCH}?{query}"
        cached = self.conn.execute(
            "SELECT response FROM ai_cache WHERE key=?",
            (f"{self.name}:{cache_key}:{{}}"[:200],)).fetchone()
        if not cached:
            self._wait()
        data = self.cached_get(SEARCH, params)
        if not data:
            return ConnectorResult(self.name, [],
                                   f"no coordinates found for '{query}'", False)
        record = data[0] if isinstance(data, list) else data
        if not record or "lat" not in record:
            return ConnectorResult(self.name, [],
                                   f"no coordinates found for '{query}'", False)

        url = (f"https://www.openstreetmap.org/"
               f"{record.get('osm_type', 'node')}/{record.get('osm_id', '')}")
        evidence = (f"OpenStreetMap places '{record.get('display_name', query)}' "
                    f"at {record['lat']}, {record['lon']}")
        found = [
            ConnectorValue("latitude", str(record["lat"]), evidence, url, self.tier),
            ConnectorValue("longitude", str(record["lon"]), evidence, url, self.tier),
        ]
        if record.get("osm_id"):
            found.append(ConnectorValue("osm_id", str(record["osm_id"]), evidence,
                                        url, self.tier, kind="identifier"))
        if record.get("type"):
            found.append(ConnectorValue("place_type", str(record["type"]), evidence,
                                        url, self.tier))
        if record.get("display_name"):
            found.append(ConnectorValue("alias", str(record["display_name"]).split(",")[0],
                                        evidence, url, self.tier, kind="alias"))
        return ConnectorResult(self.name, found,
                               f"{record.get('display_name', query)[:60]}")

    def _wait(self) -> None:
        gap = time.time() - GeocodeConnector._last_call
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)
        GeocodeConnector._last_call = time.time()
