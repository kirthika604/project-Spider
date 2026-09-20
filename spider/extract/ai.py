"""Evidence-locked AI extraction through the Claude API (section 12, step 2).

Every answer is cached by page hash so re-runs are free and the demo can run
offline. If no key is set, Spider says so once and falls back to rules.
"""

from __future__ import annotations

import hashlib
import json
import os
import re

from ..store.db import jdump, now

DEFAULT_MODEL = "claude-haiku-4-5-20251001"

SYSTEM = (
    "You extract facts from web pages into a fixed schema. You never guess. "
    "For every value you must copy an exact sentence from the page that states "
    "it, word for word, as the quote. If the page does not state a value, "
    "return null for that field. Return JSON only."
)

TEMPLATE = """Page URL: {url}
Page title: {title}

Schema to fill:
{schema}

Page text (may be truncated):
---
{text}
---

Return JSON exactly in this shape:
{{
  "records": [
    {{"type": "<entity type>",
      "fields": {{"<field>": {{"value": <value or null>, "unit": <unit or null>,
                              "quote": "<exact sentence from the page>"}}}}}}
  ],
  "relations": [
    {{"from": "<entity type>:<identifying value>", "name": "<relation>",
      "to": "<entity type>:<identifying value>",
      "quote": "<exact sentence from the page>"}}
  ]
}}
Rules: use null when the page does not state the value; never invent a quote;
the quote must contain the value; keep values as written on the page."""


class AIUnavailable(Exception):
    pass


def api_key() -> str | None:
    return os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("CLAUDE_API_KEY")


def load_dotenv(root) -> None:
    """Read .env so keys stay out of spider.yaml (section 8)."""
    path = root / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def available() -> bool:
    if not api_key():
        return False
    try:
        import anthropic  # noqa: F401
        return True
    except ImportError:
        return False


def schema_prompt(spec) -> str:
    lines = []
    for name, ent in spec.entities.items():
        parts = []
        for fname, fld in ent.fields.items():
            bits = [fld.type]
            if fld.unit:
                bits.append(f"in {fld.unit}")
            if fld.multiple:
                bits.append("may repeat")
            if fld.vocabulary:
                bits.append(f"from the '{fld.vocabulary}' list")
            parts.append(f"    {fname}: {', '.join(bits)}")
        lines.append(f"  {name} (identified by {', '.join(ent.identity) or 'name'}):")
        lines.extend(parts)
    for rel in spec.relations:
        lines.append(f"  relation: {rel.from_entity} {rel.name} {rel.to_entity}")
    return "\n".join(lines)


class AIExtractor:
    def __init__(self, conn, spec, model: str | None = None, max_calls: int = 100,
                 max_chars: int = 12000):
        self.conn = conn
        self.spec = spec
        self.model = model or spec.ai.get("model") or DEFAULT_MODEL
        self.max_calls = max_calls
        self.max_chars = max_chars
        self.calls = 0
        self.cache_hits = 0
        self.errors: list[str] = []
        self._client = None
        self._schema = schema_prompt(spec)

    def client(self):
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:
                raise AIUnavailable("the `anthropic` package is not installed "
                                    "(pip install 'project-spider[ai]')") from exc
            if not api_key():
                raise AIUnavailable("no ANTHROPIC_API_KEY - add it to .env")
            self._client = anthropic.Anthropic(api_key=api_key())
        return self._client

    def _cache_key(self, page_hash: str) -> str:
        blob = f"{self.model}|{page_hash}|{self._schema}"
        return hashlib.sha256(blob.encode()).hexdigest()[:32]

    def extract(self, page_row) -> dict | None:
        """One call per page, cached by page hash. Returns the parsed JSON."""
        page_hash = page_row["content_hash"] or hashlib.sha256(
            (page_row["text"] or "").encode()).hexdigest()[:32]
        key = self._cache_key(page_hash)
        cached = self.conn.execute(
            "SELECT response FROM ai_cache WHERE key=?", (key,)).fetchone()
        if cached:
            self.cache_hits += 1
            try:
                return json.loads(cached["response"])
            except ValueError:
                pass
        if self.calls >= self.max_calls:
            return None

        prompt = TEMPLATE.format(
            url=page_row["url"], title=page_row["title"] or "",
            schema=self._schema, text=(page_row["text"] or "")[:self.max_chars])
        try:
            message = self.client().messages.create(
                model=self.model, max_tokens=2000, system=SYSTEM,
                messages=[{"role": "user", "content": prompt}])
            self.calls += 1
            text = "".join(block.text for block in message.content
                           if getattr(block, "type", "") == "text")
            data = _parse_json(text)
        except AIUnavailable:
            raise
        except Exception as exc:
            self.errors.append(f"{page_row['url']}: {type(exc).__name__}: {exc}"[:200])
            return None
        if data is None:
            self.errors.append(f"{page_row['url']}: model did not return JSON")
            return None
        self.conn.execute(
            "INSERT OR REPLACE INTO ai_cache(key,kind,response,created_at) VALUES(?,?,?,?)",
            (key, "extract", jdump(data), now()))
        self.conn.commit()
        return data


def _parse_json(text: str):
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return None
    try:
        return json.loads(text[start:end + 1])
    except ValueError:
        return None


SUMMARY_PROMPT = """Summarise what this page says, in at most {sentences} \
sentences, for someone deciding whether it is useful for a dataset about:
{topic}

Say only what the page states. No opinions, no speculation.

Page title: {title}
Page text:
---
{text}
---"""

RELEVANCE_PROMPT = """A dataset needs these things:
{schema}

Here is a page. Answer with JSON only:
{{"useful": true|false, "why": "<at most 12 words>"}}

"useful" is true only if the page states at least one value the dataset asks
for. A page that merely mentions the topic is not useful.

Page title: {title}
Page text (start):
---
{text}
---"""


def summarise(conn, page_row, topic: str, model: str | None = None,
              sentences: int = 3) -> str | None:
    """A short summary of one page, cached by page hash (FR-14)."""
    page_hash = page_row["content_hash"] or hashlib.sha256(
        (page_row["text"] or "").encode()).hexdigest()[:32]
    key = hashlib.sha256(f"summary|{model or DEFAULT_MODEL}|{page_hash}|{topic}"
                         .encode()).hexdigest()[:32]
    cached = conn.execute("SELECT response FROM ai_cache WHERE key=?", (key,)).fetchone()
    if cached:
        return json.loads(cached["response"]).get("summary")
    try:
        import anthropic
    except ImportError as exc:
        raise AIUnavailable("the `anthropic` package is not installed") from exc
    if not api_key():
        raise AIUnavailable("no ANTHROPIC_API_KEY - add it to .env")
    client = anthropic.Anthropic(api_key=api_key())
    message = client.messages.create(
        model=model or DEFAULT_MODEL, max_tokens=300,
        system="You summarise web pages plainly and briefly.",
        messages=[{"role": "user", "content": SUMMARY_PROMPT.format(
            sentences=sentences, topic=topic, title=page_row["title"] or "",
            text=(page_row["text"] or "")[:8000])}])
    text = "".join(b.text for b in message.content
                   if getattr(b, "type", "") == "text").strip()
    if not text:
        return None
    conn.execute(
        "INSERT OR REPLACE INTO ai_cache(key,kind,response,created_at) VALUES(?,?,?,?)",
        (key, "summary", jdump({"summary": text}), now()))
    conn.commit()
    return text


def is_useful(conn, title: str, text: str, schema: str,
              model: str | None = None) -> tuple[bool, str]:
    """Layer 3: ask whether a page actually holds facts the schema wants."""
    key = hashlib.sha256(f"useful|{model or DEFAULT_MODEL}|{schema}|{title}|"
                         f"{text[:2000]}".encode()).hexdigest()[:32]
    cached = conn.execute("SELECT response FROM ai_cache WHERE key=?", (key,)).fetchone()
    if cached:
        answer = json.loads(cached["response"])
        return bool(answer.get("useful")), str(answer.get("why", ""))
    answer = ask_json(
        RELEVANCE_PROMPT.format(schema=schema, title=title or "", text=text[:3000]),
        "You judge whether a page is useful for a dataset. JSON only.",
        model=model, max_tokens=200)
    conn.execute(
        "INSERT OR REPLACE INTO ai_cache(key,kind,response,created_at) VALUES(?,?,?,?)",
        (key, "relevance", jdump(answer), now()))
    conn.commit()
    return bool(answer.get("useful")), str(answer.get("why", ""))


def ask_json(prompt: str, system: str = "Return JSON only.", model: str | None = None,
             max_tokens: int = 1500):
    """One-off structured call, used by `describe` and derivation suggestions."""
    try:
        import anthropic
    except ImportError as exc:
        raise AIUnavailable("the `anthropic` package is not installed") from exc
    if not api_key():
        raise AIUnavailable("no ANTHROPIC_API_KEY - add it to .env")
    client = anthropic.Anthropic(api_key=api_key())
    message = client.messages.create(
        model=model or DEFAULT_MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": prompt}])
    text = "".join(b.text for b in message.content if getattr(b, "type", "") == "text")
    data = _parse_json(text)
    if data is None:
        raise AIUnavailable("the model did not return usable JSON")
    return data
