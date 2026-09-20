"""GBIF: real species names, accepted synonyms and a standard identifier."""

from __future__ import annotations

from .base import Connector, ConnectorResult, ConnectorValue

API = "https://api.gbif.org/v1/species/match"


class GBIFConnector(Connector):
    name = "gbif"
    tier = 1
    uses = ("validate_scientific_name", "accepted_name", "identifier")

    def applies_to(self, entity_type: str, spec) -> bool:
        """Only entities identified by a scientific name, unless `for:` says more."""
        if self.config.get("for") or self.config.get("entities"):
            return super().applies_to(entity_type, spec)
        from ..standardize.names import is_scientific_field
        ent = spec.entities.get(entity_type)
        keys = ent.identity or (list(ent.fields)[:1] if ent else [])
        return any(is_scientific_field(k) for k in keys)

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        data = self.cached_get(API, {"name": name, "strict": "false"})
        if not data or data.get("matchType") in (None, "NONE"):
            return ConnectorResult(self.name, [], f"GBIF has no match for '{name}'", False)
        out = []
        accepted = data.get("species") or data.get("scientificName")
        usage_key = data.get("usageKey")
        url = f"https://www.gbif.org/species/{usage_key}" if usage_key else ""
        evidence = (f"GBIF match {data.get('matchType')} "
                    f"(confidence {data.get('confidence')}): {accepted}")
        if accepted:
            out.append(ConnectorValue("scientific_name", accepted, evidence, url,
                                      self.tier))
        if usage_key:
            out.append(ConnectorValue("gbif_id", str(usage_key), evidence, url,
                                      self.tier, kind="identifier"))
        for rank in ("family", "genus", "order", "kingdom"):
            if data.get(rank):
                out.append(ConnectorValue(rank, data[rank], evidence, url, self.tier))
        return ConnectorResult(self.name, out,
                               f"GBIF: {accepted} ({data.get('status', 'unknown')})")
