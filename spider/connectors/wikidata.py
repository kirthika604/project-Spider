"""Wikidata: names in many languages and linked identifiers (powers D4)."""

from __future__ import annotations

from .base import Connector, ConnectorResult, ConnectorValue

SEARCH = "https://www.wikidata.org/w/api.php"


class WikidataConnector(Connector):
    name = "wikidata"
    tier = 2
    uses = ("aliases", "identifiers")

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        languages = self.config.get("languages") or ["en", "hi", "ta"]
        found = self.cached_get(SEARCH, {
            "action": "wbsearchentities", "search": name, "language": "en",
            "format": "json", "limit": 1, "type": "item"})
        results = (found or {}).get("search") or []
        if not results:
            return ConnectorResult(self.name, [], f"Wikidata has no item for '{name}'",
                                   False)
        item_id = results[0]["id"]
        url = f"https://www.wikidata.org/wiki/{item_id}"
        entity = self.cached_get(SEARCH, {
            "action": "wbgetentities", "ids": item_id, "format": "json",
            "props": "labels|aliases",
            "languages": "|".join(languages)})
        out = [ConnectorValue("wikidata_id", item_id,
                              f"Wikidata item {item_id} for '{name}'", url, self.tier,
                              kind="identifier")]
        body = ((entity or {}).get("entities") or {}).get(item_id) or {}
        for language in languages:
            label = (body.get("labels") or {}).get(language, {}).get("value")
            if label:
                out.append(ConnectorValue("alias", label,
                                          f"Wikidata label ({language}) of {item_id}",
                                          url, self.tier, kind="alias"))
            for alias in (body.get("aliases") or {}).get(language, []) or []:
                if alias.get("value"):
                    out.append(ConnectorValue("alias", alias["value"],
                                              f"Wikidata alias ({language}) of {item_id}",
                                              url, self.tier, kind="alias"))
        return ConnectorResult(self.name, out, f"Wikidata {item_id}: {len(out)-1} names")
