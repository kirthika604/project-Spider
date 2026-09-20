"""Edit spider.yaml as text, so the user's comments and layout survive.

Re-dumping the whole file through a YAML library would tidy it and delete every
comment the user wrote - including the ones explaining why a setting is what it
is. Adding one derived column should touch one place.
"""

from __future__ import annotations

import re

import yaml

TOP_LEVEL = re.compile(r"^([A-Za-z_][\w-]*)\s*:(.*)$")


def _block(name: str, body: dict, indent: int = 2) -> list[str]:
    dumped = yaml.safe_dump({name: body}, sort_keys=False, allow_unicode=True,
                            default_flow_style=False, width=100)
    pad = " " * indent
    return [pad + line if line.strip() else line for line in dumped.rstrip("\n").split("\n")]


def _section_span(lines: list[str], section: str):
    """(index of the section's line, index just past its last line)."""
    start = next((i for i, line in enumerate(lines)
                  if (m := TOP_LEVEL.match(line)) and m.group(1) == section), None)
    if start is None:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        line = lines[i]
        if line.strip() and not line.startswith((" ", "\t", "#")):
            end = i                       # the next top-level key
            break
    return start, end


def has_entry(text: str, section: str, name: str) -> bool:
    lines = text.splitlines()
    span = _section_span(lines, section)
    if span is None:
        return False
    pattern = re.compile(rf"^\s{{2}}{re.escape(name)}\s*:")
    return any(pattern.match(line) for line in lines[span[0] + 1:span[1]])


def add_entry(text: str, section: str, name: str, body: dict) -> str | None:
    """Add `name: body` under a top-level `section`, changing nothing else.

    Returns None when the section is written in a way this cannot safely edit
    (an inline mapping with content), so the caller can say so instead of
    guessing.
    """
    lines = text.splitlines()
    block = _block(name, body)
    span = _section_span(lines, section)

    if span is None:                                   # no such section yet
        tail = [""] if lines and lines[-1].strip() else []
        return "\n".join(lines + tail + [f"{section}:"] + block) + "\n"

    start, end = span
    rest = TOP_LEVEL.match(lines[start]).group(2).split("#")[0].strip()
    if rest not in ("", "{}", "~", "null"):
        return None                                    # `derived: {a: ...}` inline
    lines[start] = f"{section}:" + (
        "  #" + lines[start].split("#", 1)[1] if "#" in lines[start] else "")

    # A comment at the left margin, or a blank line, just above the next key
    # belongs to that key: the new entry goes above them, not between them.
    insert_at = end
    while insert_at > start + 1 and (not lines[insert_at - 1].strip()
                                     or lines[insert_at - 1].startswith("#")):
        insert_at -= 1
    return "\n".join(lines[:insert_at] + block + lines[insert_at:]) + "\n"
