"""An elevation API: an independent check that is not a web page.

It never overwrites a crawled value; it raises or lowers confidence and flags
a disagreement beyond the tolerance (section 15, rule 2).
"""

from __future__ import annotations

from .base import Connector, ConnectorResult, ConnectorValue

API = "https://api.open-meteo.com/v1/elevation"


class ElevationConnector(Connector):
    name = "elevation"
    tier = 1
    uses = ("check_altitude",)

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        latitude, longitude = values.get("lat"), values.get("lon")
        if latitude is None or longitude is None:
            return ConnectorResult(self.name, [], f"no coordinates for '{name}'", False)
        data = self.cached_get(API, {"latitude": latitude, "longitude": longitude})
        elevations = (data or {}).get("elevation") or []
        if not elevations:
            return ConnectorResult(self.name, [], "elevation service gave no answer", False)
        metres = float(elevations[0])
        evidence = (f"Open-Meteo elevation at {latitude}, {longitude} is "
                    f"{metres:.0f} m")
        return ConnectorResult(
            self.name,
            [ConnectorValue("elevation_m", f"{metres:.0f}", evidence,
                            f"{API}?latitude={latitude}&longitude={longitude}",
                            self.tier, unit="m", kind="check")],
            evidence)

    def disagrees(self, claimed_min, claimed_max, measured) -> bool:
        tolerance = float(self.config.get("tolerance_m", 300))
        low = claimed_min if claimed_min is not None else measured
        high = claimed_max if claimed_max is not None else low
        return not (low - tolerance <= measured <= high + tolerance)
