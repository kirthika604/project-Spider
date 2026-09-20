"""The dataset pack (D5): the dataset and its evidence as one portable file.

A pack is a plain zip, so anyone can open it. Inside is a ready SQLite
database, the provenance for every value, CSV copies, the `spider.yaml` that
produced it and a README - enough for someone else to query it and check any
value offline, on a laptop with no connection.
"""

from __future__ import annotations

import json
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .store.db import now
from .store.export import export_target
from .spec import TargetSpec

MANIFEST = "manifest.json"


@dataclass
class Pack:
    path: Path
    tables: int
    rows: int
    values: int
    sources: int


def write(conn, spec, root: Path, out: Path | None = None,
          include_pages: bool = False) -> Pack:
    """Build the pack from the current dataset."""
    out = Path(out or (root / "exports" / f"{spec.project}.spiderpack.zip"))
    out.parent.mkdir(parents=True, exist_ok=True)
    staging = root / ".spider" / "pack"
    if staging.exists():
        import shutil
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    database = export_target(conn, spec, TargetSpec(
        name="pack_database", format="sqlite",
        path=str((staging / "dataset.db").relative_to(root)),
        provenance="separate_table"), root)
    csvs = export_target(conn, spec, TargetSpec(
        name="pack_csv", format="csv", path=str((staging / "csv").relative_to(root)),
        provenance="separate_table"), root)

    values = conn.execute(
        "SELECT COUNT(*) c FROM attributes WHERE status='accepted'").fetchone()["c"]
    sources = [dict(r) for r in conn.execute(
        "SELECT domain, tier, COUNT(*) pages FROM pages GROUP BY domain, tier "
        "ORDER BY pages DESC")]
    manifest = {
        "project": spec.project,
        "made_at": now(),
        "made_by": "Project Spider",
        "mode": spec.mode,
        "normal_form": spec.storage.normal_form,
        "standardize": {"level": spec.standardize.level,
                        "on_conflict": spec.standardize.on_conflict,
                        "min_confidence": spec.standardize.min_confidence},
        "counts": {"tables": database.tables, "rows": database.rows,
                   "values": values, "records": conn.execute(
                       "SELECT COUNT(*) c FROM entities").fetchone()["c"]},
        "sources": sources,
        "contains": ["dataset.db", "csv/", "spider.yaml", "README.md",
                     "pages.db" if include_pages else None],
        "how_to_verify": (
            "Every row in the provenance table carries the source URL and the "
            "exact sentence the value came from. Open dataset.db with any SQLite "
            "tool, or re-run `spider build` from the same spider.yaml."),
    }
    manifest["contains"] = [c for c in manifest["contains"] if c]
    (staging / MANIFEST).write_text(json.dumps(manifest, indent=2, ensure_ascii=False),
                                    encoding="utf-8")
    (staging / "README.md").write_text(_readme(spec, manifest, sources),
                                       encoding="utf-8")
    if spec.path and spec.path.exists():
        (staging / "spider.yaml").write_text(spec.path.read_text(encoding="utf-8"),
                                             encoding="utf-8")
    if include_pages:
        _copy_pages(conn, staging / "pages.db")

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in sorted(staging.rglob("*")):
            if item.is_file():
                archive.write(item, item.relative_to(staging))
    import shutil
    shutil.rmtree(staging, ignore_errors=True)
    del csvs
    return Pack(out, database.tables, database.rows, values, len(sources))


def _copy_pages(conn, target: Path) -> None:
    """The pages themselves, so a value can be re-checked without the web."""
    import sqlite3
    out = sqlite3.connect(target)
    out.executescript(
        "CREATE TABLE pages (id INTEGER PRIMARY KEY, url TEXT, domain TEXT, "
        "tier INTEGER, title TEXT, text TEXT, fetched_at TEXT);")
    out.executemany(
        "INSERT INTO pages VALUES (?,?,?,?,?,?,?)",
        [(r["id"], r["url"], r["domain"], r["tier"], r["title"], r["text"],
          r["fetched_at"]) for r in conn.execute(
            "SELECT id,url,domain,tier,title,text,fetched_at FROM pages")])
    out.commit()
    out.close()


def _readme(spec, manifest, sources) -> str:
    lines = [
        f"# {spec.project} - dataset pack",
        "",
        f"Made {manifest['made_at']} by Project Spider. "
        f"{manifest['counts']['records']} records, "
        f"{manifest['counts']['values']} values.",
        "",
        "## What is in here",
        "",
        "- `dataset.db` - a SQLite database at "
        f"{manifest['normal_form']}, with a `provenance` table",
        "- `csv/` - the same tables as CSV files",
        "- `spider.yaml` - the project file that produced it",
        "- `manifest.json` - counts, sources and settings",
        "",
        "## Checking a value",
        "",
        "Every value has a row in `provenance` with its source URL, its origin",
        "(extracted, derived or inferred), its confidence, and the exact sentence",
        "it came from. Nothing here is a summary: you can read the evidence for",
        "any cell without a connection.",
        "",
        "```sql",
        "SELECT field, value, confidence, source_url, evidence",
        "FROM provenance WHERE entity = 'Saussurea obvallata';",
        "```",
        "",
        "## Where it came from",
        "",
    ]
    lines += [f"- {s['domain']} (tier {s['tier']}, {s['pages']} pages)"
              for s in sources[:30]]
    lines += ["", "Check each source's terms before republishing its content."]
    return "\n".join(lines) + "\n"


def read(path: Path) -> dict:
    """Open a pack someone sent you and describe what is inside."""
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        if MANIFEST not in names:
            raise ValueError(f"{path.name} is not a Spider pack (no {MANIFEST})")
        manifest = json.loads(archive.read(MANIFEST).decode("utf-8"))
    manifest["files"] = names
    return manifest
