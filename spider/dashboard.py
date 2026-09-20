"""The local dashboard (section 13): crawl monitor, review queue, explorer.

It reads and writes the same spider.yaml and the same SQLite file as the CLI,
so switching between them loses nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import streamlit as st

ASSETS = Path(__file__).parent / "assets"
ICON = ASSETS / "mark.svg"

from spider import __version__
from spider.theme import (chip, confidence_chip, evidence, masthead,
                          origin_chip, use_theme)
from spider.report import build_report, coverage, explain, gaps, review_items
from spider.search import search, stats
from spider.spec import Spec, SpecError
from spider.store import db as store


def project_root() -> Path:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", default=None)
    known, _ = parser.parse_known_args(sys.argv[1:])
    return Path(known.project).resolve() if known.project else store.find_project()


@st.cache_resource
def connection(root: str):
    # Streamlit reruns the script on a new thread, so the cached connection
    # must not be bound to the thread that opened it
    return store.connect(Path(root), check_same_thread=False)


def load_spec(root: Path):
    try:
        return Spec.find(root), None
    except SpecError as exc:
        return None, str(exc)


def main() -> None:
    # "auto" keeps the sidebar open on a desktop and folded away on a phone,
    # where it would otherwise cover the screen it navigates to
    st.set_page_config(page_title="Spider", page_icon=str(ICON), layout="wide",
                       initial_sidebar_state="auto")
    use_theme()
    root = project_root()
    conn = connection(str(root))
    spec, spec_error = load_spec(root)
    summary = stats(conn)

    masthead(spec, summary)

    with st.sidebar:
        st.markdown(
            f"<div class='sp-side-head'><span class='sp-side-name'>SPIDER</span>"
            f"<span class='sp-side-ver'>v{__version__}</span></div>"
            f"<div class='sp-side-path'>{root}</div>",
            unsafe_allow_html=True)
        if spec:
            st.markdown(
                f"<div class='sp-side-project'>{spec.project}</div>"
                f"<div class='sp-side-meta'>{spec.mode} mode &middot; "
                f"{spec.storage.normal_form}</div>",
                unsafe_allow_html=True)
        screen = st.radio(
            "Screen", ["Collect", "Review", "Dataset", "Schema and rules", "Search"],
            label_visibility="collapsed")
        st.divider()
        left, right = st.columns(2)
        left.metric("Pages", summary["pages"])
        right.metric("Records", summary["entities"])
        left, right = st.columns(2)
        left.metric("Values", summary["attributes"])
        right.metric("In review", summary["review_open"])

    if spec_error and screen != "Schema and rules":
        st.error(spec_error)
        return

    {"Collect": screen_collect, "Review": screen_review, "Dataset": screen_dataset,
     "Schema and rules": screen_schema, "Search": screen_search}[screen](conn, spec, root)


# ------------------------------------------------------ screen 3: collect
def screen_collect(conn, spec, root: Path) -> None:
    st.header("Collect")
    st.caption("Start a crawl and watch what Spider accepts, rejects and why.")

    left, right = st.columns([2, 1])
    with left:
        seeds = st.text_area("Seed URLs (one per line)",
                             "\n".join(spec.sources.all_seeds()), height=110)
        keywords = st.text_input("Keywords", ", ".join(spec.sources.keywords))
    with right:
        depth = st.number_input("Depth", 0, 5, spec.sources.depth)
        max_pages = st.number_input("Max pages", 1, 5000, spec.sources.max_pages)
        delay = st.number_input("Delay per domain (s)", 0.0, 10.0,
                                float(spec.sources.delay_seconds), step=0.5)

    if st.button("Start crawl", type="primary"):
        from spider.crawl.crawler import Crawler
        rules: dict[str, list[str]] = {}
        for ent in spec.entities.values():
            for name, fld in ent.fields.items():
                if fld.extract:
                    rules.setdefault(name, []).extend(fld.extract)
        progress = st.progress(0.0)
        log = st.empty()
        lines: list[str] = []
        counters = {"saved": 0, "rejected": 0}

        def on_event(kind, **info):
            if kind == "saved":
                counters["saved"] += 1
                lines.append(f"OK   {info['url'][:70]}  tier {info.get('tier')}  "
                             f"relevance {info.get('relevance', 0):.0f}")
            else:
                counters["rejected"] += 1
                lines.append(f"{kind.upper()[:4]:4} {info.get('url','')[:70]}  "
                             f"{info.get('reason','')}")
            progress.progress(min(1.0, counters["saved"] / max(1, int(max_pages))))
            log.code("\n".join(lines[-14:]))

        crawler = Crawler(
            conn, keywords=[k.strip() for k in keywords.split(",") if k.strip()],
            depth=int(depth), max_pages=int(max_pages), delay=float(delay),
            any_domain=spec.sources.follow_other_domains,
            extract_rules=rules, tier_of=spec.tier_for, on_event=on_event)
        result = crawler.run([s.strip() for s in seeds.splitlines() if s.strip()])
        st.success(f"{result.saved} pages saved from {result.seen} visited "
                   f"({result.irrelevant} irrelevant, {result.blocked} blocked by "
                   f"robots.txt, {result.failed} failed).")

    st.divider()
    st.subheader("Build the dataset")
    use_ai = st.checkbox("Use the AI extractor when a key is set", value=True)
    if st.button("Run build"):
        from spider.assemble.build import build
        with st.spinner("Extracting, standardizing, merging and deriving ..."):
            result = build(conn, spec, use_ai=use_ai)
        columns = st.columns(5)
        columns[0].metric("Pages read", result.pages_read)
        columns[1].metric("Values", result.attributes)
        columns[2].metric("Records", result.entities)
        columns[3].metric("Conflicts", result.conflicts)
        columns[4].metric("Rejected", result.rejected_quote + result.rejected_sanity)
        if result.standardization.get("changes"):
            st.write("**What standardization changed**")
            st.table([{"change": k, "count": v}
                      for k, v in result.standardization["changes"].items()])

    st.divider()
    st.subheader("Pages collected so far")
    rows = [dict(r) for r in conn.execute(
        "SELECT id, title, url, domain, tier, relevance, word_count, fetched_at "
        "FROM pages ORDER BY id DESC LIMIT 200")]
    st.dataframe(rows, use_container_width=True, hide_index=True)


# ------------------------------------------------------- screen 4: review
def screen_review(conn, spec, root: Path) -> None:
    st.header("Review")
    st.caption("Only what needs a person: conflicts, doubtful values, "
               "rejected values and suggested derivations.")
    flash = st.session_state.pop("review_flash", None)
    if flash:
        st.success(flash)
    conflicts = review_items(conn, "conflict")
    low = review_items(conn, "low_confidence")
    suggestions = review_items(conn, "suggestion")
    rejected = review_items(conn, "sanity") + review_items(conn, "strict_reject")

    tabs = st.tabs([f"Conflicts ({len(conflicts)})", f"Low confidence ({len(low)})",
                    f"Suggested ({len(suggestions)})", f"Rejected ({len(rejected)})"])

    with tabs[0]:
        if not conflicts:
            st.caption("No source disagrees with another. Nothing to decide here.")
        for item in conflicts:
            st.markdown(f"**{item['target']}** - {item['reason']}")
            for option in (item["detail"].get("options") or []):
                sources = option.get("sources") or []
                first = sources[0] if sources else {}
                st.markdown(
                    f"{chip(option.get('value'), 'extracted')} "
                    f"<span class='sp-source'>{first.get('domain', '?')} "
                    f"&middot; tier {first.get('tier', '?')}</span>",
                    unsafe_allow_html=True)
                if first.get("quote"):
                    st.markdown(evidence(first["quote"][:220], first.get("url", "")),
                                unsafe_allow_html=True)
            columns = st.columns(4)
            from spider import review as review_module

            def decide(action, i=item["id"]):
                try:
                    outcome = action(conn, i)
                except (KeyError, ValueError) as exc:
                    st.error(str(exc))
                    return
                note = outcome.get("note") if isinstance(outcome, dict) else outcome
                if isinstance(outcome, dict) and outcome.get("crawled") is not None:
                    note = (f"{note} - found {outcome['found']} url(s), "
                            f"saved {outcome['crawled']} page(s); "
                            "run `spider build` to fold them in.")
                st.session_state["review_flash"] = str(note)
                st.rerun()

            columns[0].button("Keep first",
                              key=f"c{item['id']}0",
                              on_click=decide,
                              args=(lambda c, i: review_module.keep(c, i, 1),))
            if len(item["detail"].get("options") or []) > 1:
                columns[1].button("Keep second",
                                  key=f"c{item['id']}1",
                                  on_click=decide,
                                  args=(lambda c, i: review_module.keep(c, i, 2),))
            columns[2].button("Keep both",
                              key=f"c{item['id']}2",
                              on_click=decide,
                              args=(lambda c, i: review_module.keep(c, i, "all"),))
            columns[3].button("Ask for more sources",
                              key=f"c{item['id']}3",
                              on_click=decide,
                              args=(lambda c, i: review_module.ask_more(c, spec, i),))
            st.divider()

    with tabs[1]:
        if not low:
            st.caption("Every value clears the confidence floor.")
        from spider import review as review_module
        for item in low:
            detail = item["detail"] or {}
            st.markdown(
                f"**{item['target']}** {confidence_chip(detail.get('confidence'))}",
                unsafe_allow_html=True)
            st.caption(item["reason"])
            for source in (detail.get("sources") or [])[:3]:
                st.markdown(f"<div class='sp-source'>{source}</div>",
                            unsafe_allow_html=True)
            if item.get("entity_id"):
                left, right = st.columns(2)

                def decide_low(action, i=item["id"]):
                    try:
                        outcome = action(conn, i)
                    except (KeyError, ValueError) as exc:
                        st.error(str(exc))
                        return
                    st.session_state["review_flash"] = str(
                        outcome.get("note") if isinstance(outcome, dict)
                        else outcome)
                    st.rerun()

                left.button("Keep it anyway", key=f"k{item['id']}",
                            on_click=decide_low,
                            args=(lambda c, i: review_module.keep(c, i, "keep"),))
                right.button("Leave it out", key=f"r{item['id']}",
                             on_click=decide_low,
                             args=(lambda c, i: review_module.reject(c, i),))

    with tabs[2]:
        from spider.derive import suggest as suggest_module
        for item in suggest_module.pending(conn):
            st.markdown(f"**{item['name']}** on `{item['entity_type']}` - "
                        f"would fill {item['fills']} empty cells")
            st.code(item["formula"], language="text")
            st.caption(item["explain"])
            samples = store.jload(item["samples"], []) or []
            if samples:
                st.write("Samples: " + "; ".join(samples))
            if item["suggested_by"] == "ai":
                st.warning("Proposed by AI - approval is always required.")
            columns = st.columns(3)
            if columns[0].button("Approve", key=f"a{item['name']}"):
                suggest_module.approve(conn, spec, item["name"])
                from spider.derive.engine import DeriveEngine
                DeriveEngine(conn, spec).run([item["name"]])
                spec.save()
                st.success(f"Approved and written into {spec.path.name}")
                st.rerun()
            edited = columns[1].text_input("Edit formula", item["formula"],
                                           key=f"e{item['name']}")
            if columns[1].button("Save edit", key=f"s{item['name']}"):
                suggest_module.approve(conn, spec, item["name"], edited)
                spec.save()
                st.rerun()
            if columns[2].button("Reject", key=f"r{item['name']}"):
                suggest_module.reject(conn, item["name"])
                st.rerun()
            st.divider()

    with tabs[3]:
        if not rejected:
            st.caption("Nothing was turned away.")
        for item in rejected:
            st.markdown(f"**{item['target']}** {chip('rejected', 'inferred')}",
                        unsafe_allow_html=True)
            st.caption(item["reason"])
            detail = item["detail"] or {}
            if detail.get("quote"):
                st.markdown(evidence(detail["quote"][:220], detail.get("url", "")),
                            unsafe_allow_html=True)
            elif detail.get("url"):
                st.markdown(f"<div class='sp-source'>{detail['url']}</div>",
                            unsafe_allow_html=True)


# ------------------------------------------------------ screen 5: dataset
def screen_dataset(conn, spec, root: Path) -> None:
    st.header("Dataset")
    entity_type = st.selectbox("Table", list(spec.entities))
    level = st.selectbox("Structure", ["3NF", "BCNF", "4NF", "5NF", "6NF", "0NF", "1NF"],
                         index=0)
    mode = "analysis" if level in ("0NF", "1NF", "2NF") else spec.mode

    from spider.store.normalize import NormalFormError, build_dataset
    try:
        dataset = build_dataset(conn, spec, normal_form=level, mode=mode)
    except NormalFormError as exc:
        st.error(str(exc))
        return
    if not dataset.passed:
        st.warning("This level does not pass its check, so it would not be written:")
        for problem in dataset.problems:
            st.write(f"- {problem}")

    table = dataset.table(entity_type) or dataset.tables[0]
    st.dataframe(table.as_dicts(), use_container_width=True, hide_index=True)

    st.subheader("Coverage")
    for item in coverage(conn, spec):
        if item.entity_type != entity_type:
            continue
        st.progress(item.percent / 100,
                    text=f"{item.field}: {item.percent}% ({item.filled}/{item.total})"
                         + (f" - {item.derived} derived" if item.derived else ""))

    st.subheader("Where a value came from")
    names = [r["canonical_name"] for r in conn.execute(
        "SELECT canonical_name FROM entities WHERE type=? ORDER BY 1", (entity_type,))]
    if names:
        columns = st.columns(2)
        chosen = columns[0].selectbox("Record", names)
        fields = [c.field for c in coverage(conn, spec) if c.entity_type == entity_type]
        field_name = columns[1].selectbox("Field", fields)
        result = explain(conn, spec, chosen, field_name)
        for value in result.get("values", []):
            st.markdown(
                f"<span class='sp-value'>{value['value']} "
                f"{value.get('unit') or ''}</span> "
                f"{origin_chip(value['origin'])} "
                f"{confidence_chip(value['confidence'])}",
                unsafe_allow_html=True)
            if value.get("evidence"):
                source = (value.get("sources") or [{}])[0].get("url", "")
                st.markdown(evidence(value["evidence"][:300], source),
                            unsafe_allow_html=True)
            for parent in value.get("inputs", []):
                st.markdown(
                    f"<div class='sp-source'>calculated from {parent['name']} = "
                    f"{parent['value']} ({parent['confidence']})</div>",
                    unsafe_allow_html=True)

    st.subheader("Export")
    if st.button("Write every target from spider.yaml"):
        from spider.store.export import export_all
        try:
            for export in export_all(conn, spec, root):
                st.success(f"{export.target}: {export.rows} rows -> {export.path}")
        except NormalFormError as exc:
            st.error(str(exc))

    open_gaps = gaps(conn, spec)
    if open_gaps:
        st.subheader("Empty cells")
        st.table([{"field": f"{g['entity_type']}.{g['field']}", "missing": g["missing"],
                   "of": g["total"], "for example": ", ".join(g["examples"][:3])}
                  for g in open_gaps])
        st.caption("Run `spider fill` to search for these.")


# --------------------------------------------------- schema and rules tab
def screen_schema(conn, spec, root: Path) -> None:
    st.header("Schema and rules")
    path = (spec.path if spec else root / "spider.yaml")
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    edited = st.text_area("spider.yaml", text, height=520)
    columns = st.columns(3)
    if columns[0].button("Check"):
        import tempfile

        import yaml
        try:
            yaml.safe_load(edited)
            with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as handle:
                handle.write(edited)
            candidate = Spec.load(handle.name)
            problems = candidate.validate()
            errors = [p for p in problems if p.level == "error"]
            if not problems:
                st.success("0 errors, 0 warnings.")
            for problem in problems:
                (st.error if problem.level == "error" else st.warning)(str(problem))
            if not errors:
                st.info("Valid. Save it, then crawl or build.")
        except Exception as exc:
            st.error(str(exc))
    if columns[1].button("Save", type="primary"):
        path.write_text(edited, encoding="utf-8")
        st.success(f"Saved {path}")
        st.rerun()
    if columns[2].button("Reload"):
        st.rerun()


# ----------------------------------------------------------------- search
def screen_search(conn, spec, root: Path) -> None:
    st.header("Search the collected pages")
    query = st.text_input("Query", "")
    if query:
        for item in search(conn, query, limit=20):
            st.markdown(f"**[{item['id']}] {item['title'] or item['url']}**")
            st.caption(item["url"])
            if item.get("snippet"):
                st.write(item["snippet"])
            st.divider()


main()
