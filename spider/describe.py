"""Draft a spider.yaml from a plain-language request (D1, FR-15).

With a Claude key the schema is drafted by the model; without one, Spider
falls back to a readable starter schema built from the words in the request
plus the clarifying answers, so the workflow never depends on a key.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

from .extract import ai as ai_module

STOPWORDS = {
    "i", "want", "a", "an", "the", "of", "and", "or", "with", "for", "from", "to",
    "their", "they", "them", "its", "it", "what", "where", "who", "which", "that",
    "is", "are", "was", "were", "be", "in", "on", "at", "by", "about", "data",
    "dataset", "list", "table", "row", "rows", "per", "each", "every", "one",
    "all", "some", "my", "me", "please", "get", "collect", "gather", "build",
}

TYPE_HINTS = [
    (r"altitude|elevation|height|depth", {"type": "range", "unit": "m",
                                          "sanity": [0, 9000]}),
    (r"price|cost|fee|salary", {"type": "number"}),
    (r"date|published|updated", {"type": "date"}),
    (r"month|flowering|season_month", {"type": "month", "sanity": [1, 12]}),
    (r"year", {"type": "year"}),
    (r"count|number_of|total", {"type": "number"}),
    (r"url|link|website", {"type": "url"}),
]

SYSTEM = ("You design small, clean data schemas. Answer with JSON only, using "
          "the exact shape asked for. Prefer few entities and clear names.")

PROMPT = """The user wants this dataset:
"{request}"

Draft a schema. Answer with JSON exactly in this shape:
{{
  "project": "<short-kebab-name>",
  "entities": {{
    "<entity>": {{
      "identity": ["<field that identifies a record>"],
      "fields": {{"<field>": {{"type": "text|number|range|date|month|year|bool|url",
                             "unit": "<unit or null>", "multiple": false,
                             "required": false, "vocabulary": "<name or null>"}}}}
    }}
  }},
  "relations": [{{"from": "<entity>", "name": "<verb_phrase>", "to": "<entity>"}}],
  "questions": ["<up to 5 clarifying questions, each with a suggested answer>"]
}}
Rules: one row per real-world thing; put repeated facts in their own entity and
link them with a relation; use `range` with a unit for measurements; name fields
in snake_case."""


def draft(request: str, *, mode: str = "project", languages=None, seeds=None,
          use_ai: bool = True, like=None, structure=None,
          entity=None) -> tuple[dict, list[str], str]:
    """Return (spider.yaml as a dict, clarifying questions, how it was drafted).

    `like` is an example file whose shape the output should match (channel 2),
    `structure` an existing SQL/JSON Schema/SQLite schema to carry over
    (channel 3). Both can be combined with a plain-words request, which then
    supplies the keywords and the project name.
    """
    languages = list(languages or ["en"])
    seeds = list(seeds or [])

    if like or structure:
        from .capture import from_example, from_structure
        if structure:
            shape, notes = from_structure(structure)
            how = f"imported from {Path(structure).name}"
        else:
            shape, notes = from_example(like, entity=entity)
            how = f"copied from the example {Path(like).name}"
        source_file = Path(like or structure)
        name = _project_name(request) if request else _name_from_file(source_file)
        document = _document(name, request, mode, languages, seeds,
                             shape["entities"], shape.get("relations") or [])
        from .capture import questions_for
        questions = notes + [q.ask for q in questions_for(shape)]
        return document, questions[:8], how
    if use_ai and ai_module.available():
        try:
            answer = ai_module.ask_json(PROMPT.format(request=request), SYSTEM)
            document = _from_ai(answer, request, mode, languages, seeds)
            return document, list(answer.get("questions") or [])[:5], "drafted by Claude"
        except Exception:
            pass
    document, questions = _heuristic(request, mode, languages, seeds)
    return document, questions, "drafted from your words (no AI key set)"


def _from_ai(answer: dict, request: str, mode: str, languages, seeds) -> dict:
    entities = {}
    for name, body in (answer.get("entities") or {}).items():
        fields = {}
        for field_name, spec in (body.get("fields") or {}).items():
            clean = {k: v for k, v in (spec or {}).items() if v not in (None, "", False)}
            clean.setdefault("type", "text")
            fields[field_name] = clean
        entities[name] = {"identity": body.get("identity") or list(fields)[:1],
                          "fields": fields}
    relations = [{"from": r.get("from"), "name": r.get("name"), "to": r.get("to")}
                 for r in (answer.get("relations") or [])
                 if r.get("from") in entities and r.get("to") in entities]
    return _document(answer.get("project") or _project_name(request), request, mode,
                     languages, seeds, entities, relations)


def _heuristic(request: str, mode: str, languages, seeds) -> tuple[dict, list[str]]:
    words = [w for w in re.findall(r"[a-zA-Z_]+", request.lower())
             if w not in STOPWORDS and len(w) > 2]
    unique = list(dict.fromkeys(words))
    primary = unique[0] if unique else "item"
    primary = primary.rstrip("s") if primary.endswith("s") and len(primary) > 4 else primary

    fields = {"name": {"type": "text", "required": True}}
    for word in unique[1:8]:
        field_name = word.rstrip("s") if word.endswith("s") and len(word) > 4 else word
        if field_name == primary:
            continue
        entry: dict = {"type": "text"}
        for pattern, hint in TYPE_HINTS:
            if re.search(pattern, field_name):
                entry = dict(hint)
                break
        fields[field_name] = entry

    entities = {primary: {"identity": ["name"], "fields": fields}}
    relations = []
    for word in ("region", "place", "location", "use", "category", "author", "source"):
        if any(word in w for w in unique):
            other = word
            entities[other] = {"identity": ["name"],
                               "fields": {"name": {"type": "text", "required": True}}}
            relations.append({"from": primary, "name": f"has_{other}", "to": other})
    document = _document(_project_name(request), request, mode, languages, seeds,
                         entities, relations)
    questions = [
        f"One row per what - {primary}, or {primary} and something else?",
        "Which unit should each measurement use?",
        "Which sites do you already trust? Add them under sources.seeds.",
        f"Is `mode: {mode}` right, or do you want a flat table for analysis?",
        "What should happen when two sources disagree?",
    ]
    return document, questions


def _name_from_file(path: Path) -> str:
    """Name the project after the file it was copied from, not after the
    leftovers once the common words are removed."""
    stem = re.sub(r"[^a-z0-9]+", "-", path.stem.lower()).strip("-")
    return stem or "spider-project"


def _project_name(request: str) -> str:
    words = [w for w in re.findall(r"[a-zA-Z]+", request.lower())
             if w not in STOPWORDS][:3]
    return "-".join(words) or "spider-project"


def _document(project, request, mode, languages, seeds, entities, relations) -> dict:
    return {
        "project": project,
        "mode": mode,
        "languages": languages,
        "described_as": request,
        "sources": {
            "mode": "start_here_and_discover" if seeds else "start_here",
            "seeds": seeds,
            "keywords": [w for w in re.findall(r"[a-zA-Z]+", request.lower())
                         if w not in STOPWORDS][:6],
            "depth": 2, "max_pages": 100, "delay_seconds": 1.0,
            "follow_other_domains": False,
        },
        "entities": entities,
        "relations": relations,
        "derived": {},
        "standardize": {"level": "standard", "dates": "iso8601",
                        "on_conflict": "keep_all_and_flag", "min_confidence": 0.5},
        "storage": {"normal_form": "3NF", "database": ".spider/dataset.db",
                    "keep_provenance": True},
        "output": {"formats": ["csv", "sqlite"], "include_derived": True,
                   "label_origin": True},
    }


def to_yaml(document: dict) -> str:
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=100)
