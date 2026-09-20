"""The dashboard's look: the web-slinger palette, the mark, and the masthead.

Kept out of dashboard.py so the screens stay about the data.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

ASSETS = Path(__file__).parent / "assets"

TAGLINE = "You describe the dataset. Spider goes and gets it."

# The same mark for the terminal: eight legs, a body, and nothing that needs
# colour to read.
TERMINAL_MARK = r"""
     \  \    /  /
      \__\  /__/
    ___(  oo  )___
       /  \/  \
      /   /\   \
"""


def banner(subtitle: str = "") -> str:
    """The mark plus one line, for the first thing a new project prints."""
    lines = TERMINAL_MARK.strip("\n").splitlines()
    lines.append("")
    lines.append("    P R O J E C T   S P I D E R")
    if subtitle:
        lines.append(f"    {subtitle}")
    return "\n".join(lines)


def css() -> str:
    return (ASSETS / "theme.css").read_text(encoding="utf-8")


def logo_svg() -> str:
    """The mark, flattened to one line.

    Streamlit renders HTML through a Markdown pass, and a blank line inside
    the SVG makes it insert a <p>, which knocks the leg <g> out of the SVG
    render tree and draws a spider with no legs. Collapsing the whitespace
    keeps the markup a single block.
    """
    return _inline((ASSETS / "logo.svg").read_text(encoding="utf-8"))


def _inline(markup: str) -> str:
    return re.sub(r">\s+<", "><", markup.strip()).replace("\n", " ")


def use_theme() -> None:
    """Inject the stylesheet once per run.

    Streamlit is imported here, not at the top: the terminal mark and the
    palette are part of the tool, and `spider init` must not need the
    optional dashboard dependency to print a spider.
    """
    import streamlit as st
    st.markdown(f"<style>{css()}</style>", unsafe_allow_html=True)


def chip(label: str, kind: str = "extracted") -> str:
    """A small badge: extracted, derived, inferred, ok."""
    safe = html.escape(str(label))
    return f"<span class='sp-chip sp-chip-{kind}'>{safe}</span>"


def confidence_chip(value) -> str:
    """Colour a confidence by what it means, not by a gradient."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    kind = "ok" if number >= 0.8 else ("derived" if number >= 0.5 else "inferred")
    return chip(f"{number:.2f}", kind)


def origin_chip(origin: str) -> str:
    kind = {"extracted": "extracted", "derived": "derived",
            "inferred": "inferred", "default": "default"}.get(str(origin), "extracted")
    return chip(origin, kind)


def masthead(spec, summary: dict) -> None:
    """The title block: the mark, the project, and what is waiting."""
    project = html.escape(spec.project if spec else "no project file")
    waiting = summary.get("review_open", 0)
    right = (f"<span class='sp-chip sp-chip-inferred'>{waiting} waiting</span>"
             if waiting else
             "<span class='sp-chip sp-chip-ok'>nothing waiting</span>")
    pages = summary.get("pages", 0)
    values = summary.get("attributes", 0)
    import streamlit as st
    st.markdown(_inline(f"""<div class="sp-masthead">
  <div class="sp-logo">{logo_svg()}</div>
  <div class="sp-masthead-name">
    <div class="sp-wordmark">Project <em>Spider</em></div>
    <div class="sp-tagline">{TAGLINE}</div>
  </div>
  <div class="sp-masthead-right">
    <div class="sp-mast-project">{project}</div>
    <div class="sp-mast-counts">{pages} pages &middot; {values} values</div>
    <div class="sp-mast-state">{right}</div>
  </div>
</div>"""), unsafe_allow_html=True)


def evidence(quote: str, source: str = "") -> str:
    """A page's own sentence, shown as evidence."""
    body = f"<div class='sp-quote'>{html.escape(str(quote))}</div>"
    if source:
        body += f"<div class='sp-source'>{html.escape(str(source))}</div>"
    return body
