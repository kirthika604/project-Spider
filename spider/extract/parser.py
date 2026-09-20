"""Turn HTML into the page record: metadata, headings, JSON-LD, clean text."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from bs4 import BeautifulSoup

NOISE_TAGS = ["script", "style", "nav", "header", "footer", "aside", "form",
              "noscript", "iframe", "svg", "button"]


def _soup(html: str) -> BeautifulSoup:
    for parser in ("lxml", "html.parser"):
        try:
            return BeautifulSoup(html, parser)
        except Exception:
            continue
    return BeautifulSoup(html, "html.parser")


@dataclass
class ParsedPage:
    url: str
    title: str = ""
    description: str = ""
    author: str = ""
    published: str = ""
    lang: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    links: list[str] = field(default_factory=list)
    fields: list[tuple[str, str]] = field(default_factory=list)
    structured: list[tuple[str, str]] = field(default_factory=list)

    @property
    def word_count(self) -> int:
        return len(self.text.split())

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8", "replace")).hexdigest()[:32]

    @property
    def headings_text(self) -> str:
        return " | ".join(self.headings)


def _meta(soup: BeautifulSoup, *names: str) -> str:
    for name in names:
        tag = (soup.find("meta", attrs={"name": name})
               or soup.find("meta", attrs={"property": name}))
        if tag and tag.get("content"):
            return tag["content"].strip()
    return ""


def clean_text(soup: BeautifulSoup) -> str:
    """Main text with menus, scripts and footers removed."""
    try:                                       # trafilatura does this better
        import trafilatura
        extracted = trafilatura.extract(str(soup), include_comments=False,
                                        include_tables=True)
        if extracted and len(extracted.split()) > 40:
            return re.sub(r"\n{3,}", "\n\n", extracted).strip()
    except Exception:
        pass
    body = _soup(str(soup))
    for tag in body(NOISE_TAGS):
        tag.decompose()
    text = body.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]{2,}", " ", text)).strip()


def parse(html: str, url: str, extract_rules: dict[str, str] | None = None) -> ParsedPage:
    soup = _soup(html)
    page = ParsedPage(url=url)

    page.title = (soup.title.get_text(strip=True) if soup.title else "") or \
        _meta(soup, "og:title", "twitter:title")
    page.description = _meta(soup, "description", "og:description", "twitter:description")
    page.author = _meta(soup, "author", "article:author", "dc.creator")
    page.published = _meta(soup, "article:published_time", "datePublished",
                           "date", "dc.date", "og:updated_time")
    html_tag = soup.find("html")
    page.lang = (html_tag.get("lang", "") if html_tag else "") or _meta(soup, "og:locale")
    page.headings = [h.get_text(" ", strip=True)
                     for h in soup.find_all(["h1", "h2", "h3"])
                     if h.get_text(strip=True)][:50]

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href and not href.lower().startswith(("javascript:", "mailto:", "tel:")):
            page.links.append(href)

    for block in soup.find_all("script", type="application/ld+json"):
        raw = block.string or block.get_text() or ""
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            continue
        for item in (data if isinstance(data, list) else [data]):
            if isinstance(item, dict):
                page.structured.append((str(item.get("@type", "Thing")),
                                        json.dumps(item, ensure_ascii=False)))
            if not page.published and isinstance(item, dict) and item.get("datePublished"):
                page.published = str(item["datePublished"])

    if extract_rules:
        page.fields = run_rules(soup, page, extract_rules)

    page.text = clean_text(soup)
    return page


ATTRIBUTE_RULE = re.compile(r"^(?P<selector>.+?)@(?P<attr>[A-Za-z_][\w:-]*)$")


def split_attribute_rule(rule: str):
    """`img.cover@alt` reads the `alt` attribute of what `img.cover` selects.

    Plenty of facts are not text at all: a rating is a class, a price a
    `content` attribute, a link its `href`. The part after the last `@` is the
    attribute, unless it sits inside brackets (`a[href^='mailto:x@y']`).
    """
    match = ATTRIBUTE_RULE.match(rule.strip())
    if not match or "[" in match.group("attr") or "]" in rule.split("@")[-1]:
        return rule, None
    return match.group("selector"), match.group("attr")


def _attribute_value(node, attr: str) -> str:
    value = node.get(attr)
    if value is None:
        return ""
    return " ".join(value) if isinstance(value, list) else str(value)


def run_rules(soup: BeautifulSoup, page: ParsedPage,
              rules: dict[str, str]) -> list[tuple[str, str]]:
    """Apply `-e name=CSS` rules, `selector@attr` rules and `regex:` rules (FR-7)."""
    found: list[tuple[str, str]] = []
    plain = None
    for name, rule in rules.items():
        for single in (rule if isinstance(rule, list) else [rule]):
            single = str(single)
            if single.startswith("regex:"):
                if plain is None:
                    plain = clean_text(soup)
                for match in re.finditer(single[6:], plain, re.IGNORECASE):
                    # one group means "this part is the value"; several groups
                    # means the parts belong together, as in a range, so keep
                    # the whole match and let standardization split it
                    value = (match.group(1) if len(match.groups()) == 1
                             else match.group(0))
                    found.append((name, value.strip()))
                    break
            else:
                selector, attr = split_attribute_rule(single)
                try:
                    nodes = soup.select(selector)
                except Exception:
                    continue
                for node in nodes[:20]:
                    if attr:
                        value = _attribute_value(node, attr).strip()
                    else:
                        value = (node.get("content")
                                 or node.get_text(" ", strip=True)).strip()
                    if value:
                        found.append((name, value))
    return found


def score_relevance(page: ParsedPage, keywords) -> float:
    """Up to 10 counted hits per keyword in the text, plus 5 for a title hit."""
    if not keywords:
        return 1.0
    text, title = page.text.lower(), (page.title or "").lower()
    score = 0.0
    for keyword in keywords:
        word = keyword.lower().strip()
        if not word:
            continue
        score += min(text.count(word), 10)
        if word in title:
            score += 5
    return score
