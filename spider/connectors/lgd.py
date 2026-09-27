"""Official place codes from a Local Government Directory list you loaded once.

The directory is published as a CSV (data.gov.in). Load it into the places
reference with `spider ref load places <file>`; after that this connector
needs no network. It fixes spellings (Garwhal -> Garhwal) and attaches the
official code as the place's identifier.
"""

from __future__ import annotations

from ..ref import tables as ref
from .base import Connector, ConnectorResult, ConnectorValue


class LGDConnector(Connector):
    name = "lgd"
    tier = 1
    uses = ("place_codes",)

    def applies_to(self, entity_type: str, spec) -> bool:
        if self.config.get("for") or self.config.get("entities"):
            return super().applies_to(entity_type, spec)
        return entity_type in ("place", "region", "district", "state", "location")

    def lookup(self, entity_type: str, name: str, values: dict) -> ConnectorResult:
        row = ref.place(self.conn, name)
        if row is None:
            return ConnectorResult(self.name, [],
                                   f"'{name}' is not in the loaded place list", False)
        evidence = f"LGD {row['level'] or 'place'} code {row['code']}: {row['name']}"
        out = [ConnectorValue("place_code", str(row["code"]), evidence, "",
                              self.tier, kind="identifier")]
        if row["level"]:
            out.append(ConnectorValue("level", row["level"], evidence, "", self.tier))
        if row["parent"]:
            out.append(ConnectorValue("parent", row["parent"], evidence, "", self.tier))
        return ConnectorResult(self.name, out, f"LGD: {row['name']} ({row['code']})")
