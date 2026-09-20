"""The `spider` command line (section 8 and section 11)."""

from __future__ import annotations

import argparse
import csv
import json
import sqlite3
import sys
from pathlib import Path

from . import __version__, describe as describe_module, report as report_module, search as search_module
from .spec import Spec, SpecError
from .store import db as store

EXIT_OK, EXIT_ERROR, EXIT_CHECK_FAILED = 0, 1, 2


# ------------------------------------------------------------------ output
def say(text: str = "") -> None:
    print(text)


def fail(text: str) -> int:
    print(f"error: {text}", file=sys.stderr)
    return EXIT_ERROR


def table_print(rows, columns, widths=None) -> None:
    if not rows:
        say("  (nothing yet)")
        return
    widths = widths or [max(len(str(c)),
                            *(len(str(r.get(c, ""))[:40]) for r in rows))
                        for c in columns]
    say("  " + "  ".join(str(c).upper().ljust(w) for c, w in zip(columns, widths)))
    say("  " + "  ".join("-" * w for w in widths))
    for row in rows:
        say("  " + "  ".join(str(row.get(c, "") if row.get(c) is not None else "")[:w].ljust(w)
                             for c, w in zip(columns, widths)))


def bar(fraction: float, width: int = 20) -> str:
    filled = int(round(max(0.0, min(1.0, fraction)) * width))
    return "[" + "=" * filled + " " * (width - filled) + "]"


def project_root(args) -> Path:
    if getattr(args, "project", None):
        folder = Path(args.project).resolve()
        if not store.db_path(folder).exists():
            raise store.ProjectNotFound(
                f"{folder} is not a Spider project - run `spider init` there first")
        return folder
    return store.find_project()


def load_spec(root: Path, args=None) -> Spec:
    if args is not None and getattr(args, "file", None):
        return Spec.load(args.file)
    return Spec.find(root)


# -------------------------------------------------------------------- init
def cmd_init(args) -> int:
    root = Path(args.directory or ".").resolve()
    if not args.quiet:
        from .theme import banner
        say(banner("a crawler that builds the dataset you describe"))
        say("")
    if (root / store.SPIDER_DIR / store.DB_NAME).exists() and not args.force:
        say(f"A Spider project already exists in {root}")
        say("Use --force to recreate the database (this deletes stored pages).")
        return EXIT_OK
    if args.force:
        (root / store.SPIDER_DIR / store.DB_NAME).unlink(missing_ok=True)
    store.init_project(root)
    conn = store.connect(root)
    from .ref import tables as ref_tables
    loaded = ref_tables.counts(conn)
    conn.close()
    say(f"Created {root / store.SPIDER_DIR / store.DB_NAME}")
    say("Reference data loaded: " +
        ", ".join(f"{k.replace('ref_', '')} {v}" for k, v in sorted(loaded.items()) if v))
    if not (root / "spider.yaml").exists():
        say("")
        say("Next: describe the dataset you want, for example")
        say('  spider describe "plants of Uttarakhand, where they grow, their uses"')
        say("or write spider.yaml by hand, then run `spider check`.")
    return EXIT_OK


# ---------------------------------------------------------------- describe
def cmd_describe(args) -> int:
    root = project_root(args)
    if not args.request and not args.like and not getattr(args, "from"):
        return fail('say what you want: spider describe "plants and their uses", '
                    'or --like example.csv, or --from schema.sql')
    from .capture import CaptureError
    try:
        document, questions, how = describe_module.draft(
            args.request or "", mode=args.mode,
            languages=args.languages.split(",") if args.languages else ["en"],
            seeds=args.seed or [], use_ai=not args.no_ai,
            like=args.like, structure=getattr(args, "from"), entity=args.entity)
    except CaptureError as exc:
        return fail(str(exc))
    if args.ask:
        document = _ask_questions(document)
    text = describe_module.to_yaml(document)
    target = Path(args.output or (root / "spider.yaml"))
    say(f"Draft schema ({how}):")
    say("")
    say(text)
    if questions:
        say("Questions worth answering before you crawl:")
        for question in questions:
            say(f"  - {question}")
        say("")
    if target.exists() and not args.force:
        alternative = target.with_suffix(".draft.yaml")
        alternative.write_text(text, encoding="utf-8")
        say(f"{target.name} already exists, so the draft went to {alternative.name}.")
        say("Review it, then rename it over spider.yaml.")
        return EXIT_OK
    target.write_text(text, encoding="utf-8")
    say(f"Written to {target}")
    say("Review it, then run `spider check`.")
    return EXIT_OK


def _ask_questions(document: dict) -> dict:
    """The five clarifying questions (section 14A).

    Each one lists its choices and marks the suggested answer, so pressing
    Enter is a real answer rather than a skip.
    """
    from .capture import apply_answers, questions_for

    questions = questions_for(document)
    if not sys.stdin.isatty():
        say("")
        say("Not a terminal, so these are the answers Spider assumed:")
        for question in questions:
            if question.suggested:
                say(f"  {question.ask}")
                say(f"    -> {question.suggestion_text()}")
        return apply_answers(document, {q.key: q.suggested for q in questions})

    say("")
    say(f"{len(questions)} questions. Press Enter to take the suggested answer.")
    answers = {}
    for number, question in enumerate(questions, start=1):
        say("")
        say(f"  {number}. {question.ask}")
        for index, (value, meaning) in enumerate(question.options, start=1):
            mark = " (suggested)" if value == question.suggested else ""
            if value:
                say(f"       {index}) {value:<20} {meaning}{mark}")
            else:
                say(f"       {index}) {meaning}{mark}")
        if question.free_text:
            say("       (type them separated by spaces, or press Enter to skip)")
        try:
            given = input("     > ").strip()
        except EOFError:
            given = ""
        if not given:
            answers[question.key] = question.suggested
            continue
        if question.options and given.isdigit():
            index = int(given) - 1
            if 0 <= index < len(question.options):
                answers[question.key] = question.options[index][0]
                continue
            say("")
            say(f"       there is no choice {given}, so Spider took the "
                f"suggestion: {question.suggestion_text()}")
            answers[question.key] = question.suggested
            continue
        answers[question.key] = given
    say("")
    return apply_answers(document, answers)


# -------------------------------------------------------------------- check
def cmd_check(args) -> int:
    root = project_root(args)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        return fail(str(exc))
    conn = None
    try:
        conn = store.connect(root)
    except Exception:
        pass                       # checking a file without a project is fine
    problems = spec.validate(conn)
    if conn is not None:
        conn.close()
    errors = [p for p in problems if p.level == "error"]
    warnings = [p for p in problems if p.level == "warning"]
    say(f"Checking {spec.path or 'spider.yaml'} ...")
    say("")
    for problem in problems:
        say(str(problem))
    if problems:
        say("")
    say(f"{len(errors)} error(s), {len(warnings)} warning(s).")
    if not errors:
        say("")
        say(f"Project '{spec.project}', mode {spec.mode}, "
            f"storing at {spec.storage.normal_form}.")
        say(f"Entities: " + ", ".join(
            f"{name} ({len(ent.fields)} fields)" for name, ent in spec.entities.items()))
        if spec.relations:
            say("Relations: " + ", ".join(
                f"{r.from_entity} {r.name} {r.to_entity}" for r in spec.relations))
        if spec.derived:
            say("Derived: " + ", ".join(spec.derived))
        say("")
        say("The file is valid. Next: `spider crawl`.")
    return EXIT_CHECK_FAILED if errors else EXIT_OK


# -------------------------------------------------------------------- crawl
def _crawl_printer(verbose: bool):
    def on_event(kind, **info):
        if kind == "saved":
            say(f"  OK   {info['url'][:70]}  (relevance {info['relevance']:.0f}, "
                f"tier {info.get('tier', 3)})")
        elif verbose and kind == "blocked":
            say(f"  BLOCK {info['url'][:70]}  {info.get('reason','')}")
        elif verbose and kind in ("failed", "irrelevant", "skip"):
            say(f"  {kind[:5].upper():5} {info['url'][:70]}  {info.get('reason','')}")
    return on_event


def cmd_crawl(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    from .crawl.crawler import Crawler, crawl_from_spec

    extract_rules: dict[str, list[str]] = {}
    for rule in args.extract or []:
        if "=" not in rule:
            return fail(f"extraction rule '{rule}' must look like name=CSS_SELECTOR")
        name, selector = rule.split("=", 1)
        extract_rules.setdefault(name.strip(), []).append(selector.strip())

    if args.urls:
        crawler = Crawler(
            conn, keywords=args.keyword or [], depth=args.depth, max_pages=args.max_pages,
            delay=args.delay, any_domain=args.any_domain,
            domains=(args.domains.split(",") if args.domains else None),
            extract_rules=extract_rules, refresh=args.refresh,
            respect_robots=not args.ignore_robots, on_event=_crawl_printer(args.verbose))
        say(f"Crawling {len(args.urls)} seed(s), depth {args.depth}, "
            f"max {args.max_pages} pages, {args.delay}s per domain ...")
        result = crawler.run(args.urls)
        blocked = crawler.politeness.blocked
    else:
        try:
            spec = load_spec(root, args)
        except SpecError as exc:
            conn.close()
            return fail(f"{exc}\nOr pass seed URLs directly: spider crawl <url> ...")
        problems = [p for p in spec.validate() if p.level == "error"]
        if problems:
            conn.close()
            say("spider.yaml has errors - run `spider check` first:")
            for problem in problems[:5]:
                say(str(problem))
            return EXIT_CHECK_FAILED
        for item in spec.sources.items:
            if item.type not in ("website",):
                from . import sources as sources_module
                result = sources_module.read_source(conn, spec, item, root)
                say(f"  FILE {item.id}: {result.note}")
        say(f"Crawling from spider.yaml: {len(spec.sources.all_seeds())} seed(s), "
            f"depth {spec.sources.depth}, max {args.max_pages or spec.sources.max_pages} "
            f"pages ...")
        result = crawl_from_spec(conn, spec, max_pages=args.max_pages,
                                 depth=args.depth if args.depth != 2 else None,
                                 refresh=args.refresh,
                                 on_event=_crawl_printer(args.verbose),
                                 use_ai_relevance=args.ai_relevance)
        blocked = {}

    say("")
    say(f"Crawl #{result.crawl_id}: {result.saved} pages saved from "
        f"{result.seen} visited.")
    details = []
    if result.irrelevant:
        details.append(f"{result.irrelevant} skipped as irrelevant")
    if result.skipped_existing:
        details.append(f"{result.skipped_existing} already stored")
    if result.blocked:
        details.append(f"{result.blocked} blocked by robots.txt")
    if result.failed:
        details.append(f"{result.failed} failed")
    if details:
        say("  " + ", ".join(details) + ".")
    for domain, reason in list(blocked.items())[:5]:
        say(f"  blocked: {domain} ({reason})")
    for error in result.errors[:5]:
        say(f"  failed: {error[:100]}")
    conn.close()
    say("")
    say("Next: `spider build` to assemble the dataset, or `spider search \"...\"`.")
    return EXIT_OK


# ------------------------------------------------------------------- search
def cmd_search(args) -> int:
    conn = store.connect(project_root(args))
    results = search_module.search(conn, args.query, limit=args.limit,
                                   domain=args.domain)
    if not results:
        say(f"No stored page matches '{args.query}'.")
        conn.close()
        return EXIT_OK
    say(f"{len(results)} result(s) for '{args.query}':")
    say("")
    for item in results:
        say(f"[{item['id']}] {item['title'] or '(no title)'}")
        say(f"     {item['url']}")
        snippet = (item.get("snippet") or "").replace("\n", " ")
        if snippet:
            say(f"     {snippet[:240]}")
        say("")
    conn.close()
    return EXIT_OK


def cmd_get(args) -> int:
    conn = store.connect(project_root(args))
    page = search_module.get_page(conn, args.page_id)
    conn.close()
    if not page:
        return fail(f"no page with id {args.page_id}")
    if args.json:
        say(json.dumps(page, indent=2, ensure_ascii=False, default=str))
        return EXIT_OK
    say(f"[{page['id']}] {page['title']}")
    say(f"URL       {page['url']}")
    say(f"Domain    {page['domain']} (tier {page['tier']})")
    say(f"Fetched   {page['fetched_at']}   words {page['word_count']}   "
        f"relevance {page['relevance']}")
    if page["author"] or page["published"]:
        say(f"Author    {page['author'] or '-'}    published {page['published'] or '-'}")
    if page["description"]:
        say(f"Summary   {page['description'][:200]}")
    if page.get("summary"):
        say("")
        say("AI summary:")
        for line in str(page["summary"]).splitlines():
            say(f"  {line}")
    if page["fields"]:
        say("")
        say("Custom fields:")
        for item in page["fields"]:
            say(f"  {item['name']:20} {str(item['value'])[:80]}")
    if page["structured"]:
        say("")
        say(f"Structured data: {', '.join(s['type'] for s in page['structured'])}")
    if page["values"]:
        say("")
        say("Dataset values from this page:")
        for value in page["values"]:
            say(f"  {value['canonical_name']}.{value['name']} = {value['value']} "
                f"({value['confidence']})")
    say("")
    say(page["text"][:args.chars] + ("..." if len(page["text"]) > args.chars else ""))
    return EXIT_OK


def cmd_stats(args) -> int:
    conn = store.connect(project_root(args))
    data = search_module.stats(conn)
    conn.close()
    say(f"Pages       {data['pages']} from {data['domains']} domains "
        f"({data['words']:,} words)")
    say(f"Links       {data['links']}")
    say(f"Crawls      {data['crawls']}")
    say(f"Entities    {data['entities']}    values {data['attributes']}    "
        f"relations {data['relations']}")
    if data["review_open"]:
        say(f"Review      {data['review_open']} item(s) waiting - `spider report`")
    if data["top_domains"]:
        say("")
        say("Top domains:")
        table_print(data["top_domains"], ["domain", "pages"])
    if data["top_fields"]:
        say("")
        say("Most filled fields:")
        table_print(data["top_fields"], ["name", "n"])
    return EXIT_OK


# ---------------------------------------------------------------------- sql
def cmd_sql(args) -> int:
    root = project_root(args)
    query = args.query.strip()
    lowered = query.lower().lstrip("(")
    if not (lowered.startswith("select") or lowered.startswith("with")):
        return fail("the sql command is read-only - it runs SELECT (or WITH) queries only")
    path = store.db_path(root)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(query).fetchall()
    except sqlite3.Error as exc:
        conn.close()
        return fail(f"SQLite: {exc}")
    conn.close()
    if not rows:
        say("(no rows)")
        return EXIT_OK
    columns = rows[0].keys()
    if args.format == "json":
        say(json.dumps([dict(r) for r in rows], indent=2, ensure_ascii=False, default=str))
    elif args.format == "csv":
        writer = csv.writer(sys.stdout)
        writer.writerow(columns)
        writer.writerows([list(r) for r in rows])
    else:
        table_print([dict(r) for r in rows], list(columns))
        say("")
        say(f"{len(rows)} row(s)")
    return EXIT_OK


# ------------------------------------------------------------------ preview
def cmd_preview(args) -> int:
    """Run the whole chain on 3 to 5 pages and show the rows (section 14A).

    This catches wrong field names, wrong units and impossible requests before
    a full crawl spends time and AI calls on them.
    """
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    problems = [p for p in spec.validate() if p.level == "error"]
    if problems:
        conn.close()
        say("spider.yaml has errors - run `spider check` first:")
        for problem in problems[:5]:
            say(str(problem))
        return EXIT_CHECK_FAILED

    seeds = spec.sources.all_seeds()
    if not seeds:
        conn.close()
        return fail("no sources to preview - add seeds to spider.yaml, or "
                    "`spider source add <link-or-file>`")

    from .assemble.build import build
    from .crawl.crawler import crawl_from_spec
    from .store.normalize import NormalFormError, build_dataset

    say(f"Preview: reading {args.pages} page(s) from your trusted sources ...")
    # always re-fetch the sample, so a preview shows what those pages say now
    # depth 1, because a seed is often an index page that states no values
    result = crawl_from_spec(conn, spec, max_pages=args.pages, depth=1, kind="preview",
                             refresh=True, on_event=_crawl_printer(False))
    stored = conn.execute("SELECT COUNT(*) c FROM pages").fetchone()["c"]
    if not result.saved and not stored:
        conn.close()
        say("None of the seed pages could be read, so there is nothing to preview.")
        for error in result.errors[:3]:
            say(f"  {error[:100]}")
        return EXIT_ERROR
    if not result.saved:
        say(f"  (none of the seeds could be re-read; previewing the "
            f"{stored} page(s) already stored)")

    report = build(conn, spec, use_ai=not args.no_ai,
                   page_ids=result.page_ids or None,
                   limit=None if result.page_ids else args.pages)
    say("")
    say(f"{result.saved} page(s) read, {report.attributes} value(s) found.")
    try:
        dataset = build_dataset(conn, spec)
    except NormalFormError as exc:
        conn.close()
        return fail(str(exc))

    for table in dataset.tables:
        if table.kind != "entity" or not table.rows:
            continue
        say("")
        say(f"{table.name} (first {min(len(table.rows), args.rows)} of {len(table.rows)}):")
        table_rows = [dict(zip(table.columns, row)) for row in table.rows[:args.rows]]
        columns = [c for c in table.columns if any(r.get(c) is not None
                                                   for r in table_rows)][:6]
        table_print(table_rows, columns)

    empty = [c for c in report_module.coverage(conn, spec) if c.filled == 0]
    if empty:
        say("")
        say("Fields nothing filled in this sample:")
        for item in empty:
            field = spec.entity_field(item.entity_type, item.field)
            hint = ("no extraction rule and no AI key - add `extract:` or set "
                    "ANTHROPIC_API_KEY" if field and not field.extract
                    else "these pages may not state it; try other sources or "
                         "`spider fill`")
            if item.field in spec.derived:
                hint = "derived - it fills once its inputs do"
            say(f"  {item.entity_type}.{item.field:22} {hint}")
    # a preview builds from its sample, which would otherwise leave the
    # project holding a dataset made from five pages
    total = conn.execute(
        "SELECT COUNT(*) c FROM pages WHERE relevance > 0").fetchone()["c"]
    used = len(result.page_ids) if result.page_ids else min(args.pages, total)
    if total > used:
        build(conn, spec, use_ai=not args.no_ai)
        say("")
        say(f"(the full dataset was rebuilt from all {total} stored pages, so the "
            f"preview left nothing behind)")

    say("")
    say("If this is the shape you wanted, run `spider crawl` for the full set. "
        "If not, edit spider.yaml and preview again.")
    conn.close()
    return EXIT_OK


# -------------------------------------------------------------------- build
def cmd_build(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    problems = [p for p in spec.validate() if p.level == "error"]
    if problems:
        conn.close()
        say("spider.yaml has errors - run `spider check` first:")
        for problem in problems[:5]:
            say(str(problem))
        return EXIT_CHECK_FAILED

    from .assemble.build import build
    from .store.db import jdump
    from .store.normalize import NormalFormError, build_dataset

    if args.normalize:
        spec.storage.normal_form = args.normalize.upper()
        spec.raw.setdefault("storage", {})["normal_form"] = spec.storage.normal_form
    if args.mode:
        spec.mode = args.mode

    say(f"Building '{spec.project}' from stored pages ...")
    result = build(conn, spec, use_ai=not args.no_ai, limit=args.limit,
                   use_connectors=not args.no_connectors)

    say("")
    say(f"Pages read        {result.pages_read}")
    say(f"Candidate values  {result.candidates}")
    if result.rejected_quote or result.rejected_sanity or result.rejected_vocab:
        say(f"Rejected          {result.rejected_quote} no quote proof, "
            f"{result.rejected_sanity} failed sanity, "
            f"{result.rejected_vocab} not in vocabulary")
    say(f"Entities          {result.entities}")
    say(f"Values stored     {result.attributes}")
    say(f"Relations         {result.relations}")
    if result.aliases:
        say(f"Names and aliases {result.aliases}")
    if result.derived:
        say(f"Derived values    {result.derived}")
    if result.defaults:
        say(f"Defaults filled   {result.defaults}  (declared in spider.yaml, "
            f"not found on any page)")
    if result.conflicts:
        say(f"Conflicts         {result.conflicts} (kept and flagged)")
    if result.low_confidence:
        say(f"In review         {result.low_confidence}")
    if result.decisions_applied:
        say(f"Review decisions  {result.decisions_applied} reapplied from earlier")
    if result.ai_calls or result.ai_cache_hits:
        say(f"AI calls          {result.ai_calls} ({result.ai_cache_hits} from cache)")
    for item in (result.connectors.get("connectors") or []):
        say(f"Connector         {item['name']}: {item['calls']} call(s) "
            f"({item['cache_hits']} cached), {item['values']} value(s), "
            f"{item['identifiers']} identifier(s)")
    for problem in result.connector_errors:
        say(f"  connector: {problem}")
    if result.merges:
        say("")
        say("Merged records:")
        for merge in result.merges[:5]:
            say(f"  {merge}")
    changes = result.standardization.get("changes") or {}
    if changes:
        say("")
        say("Standardization:")
        for change, count in list(changes.items())[:8]:
            example = (result.standardization.get("examples") or {}).get(change, [])
            suffix = f"   e.g. {example[0]}" if example else ""
            say(f"  {count:4}  {change}{suffix}")
    for error in result.derive_errors[:5]:
        say(f"  derivation: {error}")

    # suggested derivations (FR-25)
    if spec.derive_policy != "off":
        from .derive import suggest as suggest_module
        suggestions = suggest_module.find(conn, spec, use_ai=not args.no_ai)
        if spec.derive_policy == "auto_safe":
            applied = [s for s in suggestions if s.safe]
            suggest_module.save(conn, suggestions)
            for suggestion in applied:
                suggest_module.approve(conn, spec, suggestion.name)
            if applied:
                from .derive.engine import DeriveEngine
                DeriveEngine(conn, spec).run([s.name for s in applied])
                spec.save()
                say("")
                say(f"auto_safe applied {len(applied)} exact derivation(s): "
                    + ", ".join(s.name for s in applied))
            remaining = [s for s in suggestions if not s.safe]
        else:
            suggest_module.save(conn, suggestions)
            remaining = suggestions
        if remaining:
            say("")
            say(f"{len(remaining)} suggested derivation(s) waiting for you - "
                f"see `spider report` then `spider derive approve <name>`.")

    # the output tables must pass their level check before anything is written
    try:
        dataset = build_dataset(conn, spec)
    except NormalFormError as exc:
        conn.close()
        return fail(str(exc))
    conn.execute(
        "INSERT INTO build_reports(built_at,mode,normal_form,passed,changes) "
        "VALUES(?,?,?,?,?)",
        (store.now(), spec.mode, dataset.normal_form, int(dataset.passed),
         jdump(result.standardization)))
    conn.commit()

    say("")
    if dataset.passed:
        say(f"{dataset.normal_form} check passed: "
            + ", ".join(f"{t.name} ({len(t.rows)})" for t in dataset.tables))
    else:
        say(f"{dataset.normal_form} check FAILED - the dataset was not written:")
        for problem in dataset.problems[:8]:
            say(f"  - {problem}")
        conn.close()
        return EXIT_CHECK_FAILED

    if not args.no_export:
        from .store.export import export_all
        try:
            exports = export_all(conn, spec, root)
        except NormalFormError as exc:
            conn.close()
            return fail(str(exc))
        for export in exports:
            say(f"Exported {export.target}: {export.rows} rows in {export.tables} "
                f"tables -> {export.path}")
    conn.close()
    say("")
    say("Next: `spider report` to see coverage and anything needing review.")
    return EXIT_OK


# ------------------------------------------------------------------- report
def cmd_report(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    data = report_module.build_report(
        conn, spec, Path(args.gold) if args.gold else None)

    say(f"Project '{spec.project}' - {spec.mode} mode, {spec.storage.normal_form}")
    say("")
    say("Records: " + ", ".join(f"{k} {v}" for k, v in data.entities.items()))
    say("")
    say("Coverage")
    for item in data.coverage:
        marks = []
        if item.derived:
            marks.append(f"{item.derived} derived")
        if item.defaults:
            marks.append(f"{item.defaults} by default")
        note = f"  ({', '.join(marks)})" if marks else ""
        say(f"  {item.entity_type+'.'+item.field:32} {bar(item.percent/100)} "
            f"{item.percent:5.1f}%  {item.filled}/{item.total}{note}")

    if data.conflicts:
        say("")
        say(f"Conflicts ({len(data.conflicts)}) - every value kept, none hidden")
        say("  decide with `spider review` (keep one, keep both, ask for more)")
        for conflict in data.conflicts[:args.limit]:
            say(f"  {conflict['target']}: {conflict['reason']}")
            for option in (conflict["detail"].get("options") or [])[:3]:
                sources = option.get("sources") or []
                first = sources[0] if sources else {}
                say(f"    {str(option.get('value'))[:28]:30} "
                    f"{first.get('domain','')} (tier {first.get('tier','?')})"
                    f"  \"{(first.get('quote') or '')[:60]}\"")
    if data.low_confidence:
        say("")
        say(f"Low confidence ({len(data.low_confidence)})")
        say("  keep one anyway with `spider review keep <id> keep`, or reject it")
        for item in data.low_confidence[:args.limit]:
            say(f"  {item['target']}: {item['reason']}")
    if data.rejected:
        say("")
        say(f"Rejected values ({len(data.rejected)}) - kept out of the dataset")
        for item in data.rejected[:args.limit]:
            detail = item["detail"] or {}
            say(f"  {item['target']}: {item['reason']}")
            if detail.get("url"):
                say(f"    from {detail['url'][:70]}")
    if data.suggestions:
        say("")
        say(f"Suggested derivations ({len(data.suggestions)})")
        for item in data.suggestions[:args.limit]:
            detail = item["detail"] or {}
            say(f"  {detail.get('name')} on {detail.get('on')}: "
                f"{detail.get('formula') or detail.get('bands')}")
            say(f"    {detail.get('explain','')}")
            say(f"    would fill {detail.get('fills',0)} empty cells; "
                f"samples: {'; '.join(detail.get('samples') or []) or '-'}")
            say(f"    [ spider derive approve {detail.get('name')} | edit | reject ]")
    if data.gaps:
        say("")
        say("Gaps - `spider fill` crawls for these")
        for gap in data.gaps[:args.limit]:
            say(f"  {gap['entity_type']}.{gap['field']:24} {gap['missing']} missing "
                f"of {gap['total']}  e.g. {', '.join(gap['examples'][:3])}")
    say("")
    say("Source health")
    table_print([{k: v for k, v in s.items() if k in
                  ("domain", "tier", "pages", "values_given", "values_agreed",
                   "in_review", "avg_confidence")}
                 for s in data.sources[:args.limit]],
                ["domain", "tier", "pages", "values_given", "values_agreed",
                 "in_review", "avg_confidence"])
    say("  values_agreed: values this source confirmed that another source "
        "stored first")

    from . import sources as sources_module
    file_sources = [s for s in sources_module.health(conn) if s["type"] != "website"]
    if file_sources:
        say("")
        say("Your own sources")
        table_print(file_sources, ["id", "type", "tier", "pages_read", "values_accepted"])

    if data.gold:
        say("")
        gold = data.gold
        if gold.get("error"):
            say(f"Gold set: {gold['error']}")
        else:
            say(f"Accuracy test against {gold['total']} known facts")
            say(f"  found        {gold['found']}/{gold['total']} "
                f"({gold['coverage_percent']}% coverage)")
            say(f"  correct      {gold['correct']}/{gold['found']} "
                f"({gold['accuracy_percent']}%)")
            say(f"  high confidence (>= 0.8): "
                f"{gold['high_confidence_correct']}/{gold['high_confidence_total']} "
                f"({gold['high_confidence_accuracy']}%)  target 90%")
            for detail in gold["details"]:
                if detail["verdict"] != "correct":
                    say(f"    {detail['verdict']:9} {detail['entity']}."
                        f"{detail['field']}: expected {detail['expected']}, "
                        f"got {detail['got']}")
    conn.close()
    return EXIT_OK


# --------------------------------------------------------------------- fill
def _filled_cells(conn, spec) -> int:
    return sum(c.filled for c in report_module.coverage(conn, spec))


def cmd_fill(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))

    if args.until_stable:
        # D3: keep going until a round stops adding values
        from .assemble.build import build
        before = _filled_cells(conn, spec)
        for round_number in range(1, args.rounds + 1):
            say(f"--- round {round_number} of at most {args.rounds} "
                f"({before} cells filled) ---")
            code = _fill_once(conn, spec, root, args)
            if code != EXIT_OK:
                conn.close()
                return code
            build(conn, spec, use_ai=not getattr(args, "no_ai", False))
            after = _filled_cells(conn, spec)
            if after <= before:
                say("")
                say(f"Coverage stopped improving at {after} filled cell(s) - stopping.")
                break
            say(f"    round {round_number} filled {after - before} more cell(s).")
            before = after
        else:
            say("")
            say(f"Reached the {args.rounds}-round limit with {before} cell(s) filled.")
        conn.close()
        return EXIT_OK

    code = _fill_once(conn, spec, root, args)
    conn.close()
    return code


def _fill_once(conn, spec, root, args) -> int:
    gaps = report_module.gaps(conn, spec)
    if not gaps:
        say("No empty cells - nothing to fill.")
        return EXIT_OK

    say("Empty cells to fill:")
    for gap in gaps[:10]:
        say(f"  {gap['entity_type']}.{gap['field']}: {gap['missing']} missing")

    from .crawl.discovery import queries_for_gaps, discover
    queries = queries_for_gaps(conn, spec, gaps, limit=args.queries)
    say("")
    say(f"Search queries written from the gaps ({len(queries)}):")
    for query in queries[:10]:
        say(f"  {query}")

    urls, note = discover(conn, spec, queries, limit=args.max_pages)
    say("")
    say(note)
    if not urls:
        say("No new sources found. Add seeds to spider.yaml, or set a search key "
            "in .env (BRAVE_API_KEY, SERPER_API_KEY or TAVILY_API_KEY).")
        return EXIT_OK

    from .crawl.crawler import crawl_from_spec
    result = crawl_from_spec(conn, spec, seeds=urls, max_pages=args.max_pages,
                             depth=1, kind="fill", on_event=_crawl_printer(args.verbose))
    say("")
    say(f"Fill crawl: {result.saved} new pages saved.")
    if not getattr(args, "until_stable", False):
        say("Next: `spider build` to fold them into the dataset.")
    return EXIT_OK


# ------------------------------------------------------------------- derive
def cmd_derive(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    from .derive import suggest as suggest_module
    from .derive.engine import DeriveEngine

    if args.action == "list":
        pending = suggest_module.pending(conn)
        if not pending:
            say("No suggested derivations. Run `spider build` first.")
        for item in pending:
            say(f"{item['name']} on {item['entity_type']} "
                f"({item['method']}, would fill {item['fills']} cells)")
            say(f"  formula: {item['formula']}")
            say(f"  {item['explain']}")
            samples = store.jload(item["samples"], []) or []
            if samples:
                say(f"  samples: {'; '.join(samples)}")
            if item["suggested_by"] == "ai":
                say("  proposed by AI - approval is always required")
            say("")
        active = [d for d in spec.derived.values() if d.status == "active"]
        if active:
            say("Active derivations in spider.yaml:")
            for der in active:
                say(f"  {der.name} on {der.on}: {der.formula or der.bands or der.method}")
        conn.close()
        return EXIT_OK

    if not args.name:
        conn.close()
        return fail(f"`spider derive {args.action}` needs the name of a derivation")

    if args.action == "reject":
        suggest_module.reject(conn, args.name)
        say(f"Rejected '{args.name}'. It will not be suggested again.")
        conn.close()
        return EXIT_OK

    if args.action in ("approve", "edit"):
        formula = args.formula
        if args.action == "edit" and not formula:
            conn.close()
            return fail("`spider derive edit <name> --formula \"...\"` needs a formula")
        try:
            block = suggest_module.approve(conn, spec, args.name, formula)
        except KeyError as exc:
            conn.close()
            return fail(str(exc))
        report = DeriveEngine(conn, spec).run([args.name])
        spec.save()
        say(f"Approved '{args.name}' and wrote it into {spec.path.name}:")
        say("  " + json.dumps(block, default=str))
        say(f"Calculated {report.written} value(s)"
            + (f", {report.queued_for_review} waiting for review" if report.queued_for_review else ""))
        for error in report.errors[:5]:
            say(f"  {error}")
        conn.close()
        return EXIT_OK

    if args.action == "run":
        report = DeriveEngine(conn, spec).run([args.name] if args.name != "all" else None)
        say(f"Calculated {report.written} value(s); {report.skipped_missing} left empty "
            f"because an input was missing.")
        for error in report.errors[:5]:
            say(f"  {error}")
        conn.close()
        return EXIT_OK

    conn.close()
    return fail(f"unknown action '{args.action}'")


# ------------------------------------------------------------------ explain
def cmd_explain(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    result = report_module.explain(conn, spec, args.entity, args.field)
    conn.close()
    if result.get("error"):
        return fail(result["error"])
    say(f"{result['entity']} ({result['type']}) . {result['field']}")
    if not result["values"]:
        say("  no value stored for this field")
        return EXIT_OK
    for item in result["values"]:
        marker = {"accepted": "*", "review": "?", "superseded": "x"}.get(item["status"], "-")
        say("")
        say(f"  {marker} {item['value']}{' ' + (item['unit'] or '')}   "
            f"[{item['origin']}]  confidence {item['confidence']}  ({item['status']})")
        if item["raw_value"] and item["raw_value"] != item["value"]:
            say(f"      as written on the page: {item['raw_value']}")
        if item["evidence"]:
            say(f"      evidence: \"{item['evidence'][:160]}\"")
        for source in item["sources"]:
            say(f"      source:   {source.get('url','')}"
                + (f" (tier {source['tier']})" if source.get("tier") else ""))
        lineage = item.get("lineage") or {}
        if lineage.get("agreeing_domains"):
            say(f"      agreed by: {', '.join(lineage['agreeing_domains'])}")
        if item["origin"] in ("derived", "inferred"):
            say(f"      formula:  {lineage.get('formula')}")
            if lineage.get("explain"):
                say(f"      meaning:  {lineage['explain']}")
            for parent in item.get("inputs", []):
                say(f"      input:    {parent['name']} = {parent['value']} "
                    f"(confidence {parent['confidence']}) from {parent['url'] or 'a file'}")
    return EXIT_OK


# ------------------------------------------------------------------- review
def cmd_review(args) -> int:
    """Decide what the build flagged: conflicts and doubtful values (stage 4)."""
    from . import review as review_module
    root = project_root(args)
    conn = store.connect(root)

    if args.action == "list":
        items = review_module.open_items(conn, limit=args.limit)
        if not items:
            say("Nothing is waiting for a decision.")
            say("`spider build` flags conflicts and low-confidence values here.")
            conn.close()
            return EXIT_OK
        say(f"{len(items)} item(s) waiting:")
        say("")
        for item in items:
            options = (item["detail"] or {}).get("options") or []
            say(f"[{item['id']}] {item['kind']:14} {item['target']}")
            say(f"       {item['reason']}")
            for number, option in enumerate(options, start=1):
                sources = option.get("sources") or []
                first = sources[0] if sources else {}
                say(f"       {number}) {str(option.get('value'))[:40]:42} "
                    f"{first.get('domain', '')} (tier {first.get('tier', '?')})")
        say("")
        say("Decide with: spider review show <id>  |  keep <id> 1|all  |  "
            "reject <id>  |  ask <id>  |  forget <target>")
        conn.close()
        return EXIT_OK

    if not args.item_id:
        conn.close()
        return fail(f"`spider review {args.action}` needs the id of an item - "
                    "run `spider review` to list them")

    if args.action == "forget":
        dropped = review_module.forget(conn, args.item_id)
        conn.close()
        if not dropped:
            return fail(f"no stored decision for '{args.item_id}'")
        say(f"Dropped the decision for '{args.item_id}'. "
            "The next build will ask for it again.")
        return EXIT_OK

    try:
        item_id = int(args.item_id)
    except ValueError:
        conn.close()
        return fail(f"'{args.item_id}' is not an item id - run `spider review` "
                    "to list them, or use `review forget <target>` for a decision")

    if args.action == "show":
        item = review_module.get_item(conn, item_id)
        if not item:
            conn.close()
            return fail(f"no review item {args.item_id}")
        detail = item["detail"] or {}
        say(f"[{item['id']}] {item['kind']}  {item['target']}  ({item['status']})")
        say(f"  {item['reason']}")
        for number, option in enumerate((detail.get("options") or []), start=1):
            say("")
            say(f"  {number}) {option.get('value')}")
            for source in (option.get("sources") or []):
                tier = f" (tier {source['tier']})" if source.get("tier") else ""
                say(f"     source: {source.get('url', '')}{tier}")
                if source.get("quote"):
                    say(f"     quote:  \"{source['quote'][:160]}\"")
        for key in ("value", "asked_for"):
            if detail.get(key):
                say(f"  {key}: {detail[key]}")
        if item["kind"] == "low_confidence":
            say("")
            say("  keep it anyway:  spider review keep "
                f"{item['id']} keep")
            say(f"  leave it out:    spider review reject {item['id']}")
        conn.close()
        return EXIT_OK

    try:
        if args.action == "keep":
            choice = args.choice or 1
            result = review_module.keep(conn, item_id, choice)
        elif args.action == "reject":
            result = review_module.reject(conn, item_id)
        else:                                   # ask
            try:
                spec = load_spec(root, args)
            except SpecError as exc:
                conn.close()
                return fail(str(exc))
            result = review_module.ask_more(conn, spec, item_id)
    except KeyError as exc:
        conn.close()
        return fail(str(exc))
    except ValueError as exc:
        conn.close()
        return fail(str(exc))

    say(str(result.get("note") or result))
    if result.get("crawled") is not None:
        say(f"  found {result['found']} url(s), saved {result['crawled']} page(s); "
            "run `spider build` to fold them in.")
    remaining = conn.execute(
        "SELECT COUNT(*) c FROM review_queue WHERE status='open'").fetchone()["c"]
    conn.close()
    if remaining:
        say("")
        say(f"{remaining} item(s) still waiting - `spider review` to list them.")
    else:
        say("")
        say("The review queue is empty.")
    return EXIT_OK


# ------------------------------------------------------------------- export
def cmd_export(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    # the simple page export of the MVP
    if args.pages:
        rows = conn.execute(
            "SELECT id,url,domain,title,description,author,published,lang,word_count,"
            "relevance,fetched_at FROM pages ORDER BY id").fetchall()
        if args.format == "json":
            say(json.dumps([dict(r) for r in rows], indent=2, default=str))
        elif args.format == "csv":
            writer = csv.writer(sys.stdout)
            writer.writerow(rows[0].keys() if rows else ["id"])
            writer.writerows([list(r) for r in rows])
        else:
            for row in rows:
                say(f"[{row['id']}] {row['title']}\n{row['url']}\n")
        conn.close()
        return EXIT_OK

    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    from .store.export import export_all
    from .store.normalize import NormalFormError

    if args.pack:
        from . import pack as pack_module
        try:
            made = pack_module.write(conn, spec, root,
                                     Path(args.output) if args.output else None,
                                     include_pages=args.with_pages)
        except NormalFormError as exc:
            conn.close()
            return fail(str(exc))
        conn.close()
        size = made.path.stat().st_size / 1024
        say(f"Pack written: {made.path} ({size:.0f} KB)")
        say(f"  {made.rows} rows in {made.tables} tables, {made.values} values, "
            f"{made.sources} source domain(s)")
        say("  inside: dataset.db, csv/, spider.yaml, manifest.json, README.md"
            + (", pages.db" if args.with_pages else ""))
        say("")
        say("Anyone can open it with any zip tool and query dataset.db offline; "
             "every value keeps its source and its quote.")
        return EXIT_OK
    if args.format and not spec.output.targets:
        spec.output.formats = [args.format]
    try:
        exports = export_all(conn, spec, root, only=args.target)
    except NormalFormError as exc:
        conn.close()
        return fail(str(exc))
    for export in exports:
        say(f"{export.target:16} {export.format:7} {export.normal_form:5} "
            f"{export.rows:6} rows  -> {export.path}")
    say("")
    say("Each export folder also has metadata.json and README.md describing "
        "the sources, mode, level and cleaning settings.")
    conn.close()
    return EXIT_OK


# ---------------------------------------------------------------------- ref
def cmd_ref(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    from .ref import tables as ref_tables

    if args.action == "load":
        if args.preset:
            try:
                loaded = ref_tables.load_preset(conn, args.preset)
            except ValueError as exc:
                conn.close()
                return fail(str(exc))
            say(f"Loaded the '{args.preset}' reference data: " +
                ", ".join(f"{k} {v}" for k, v in sorted(loaded.items())))
            conn.close()
            return EXIT_OK
        if not args.files:
            loaded = ref_tables.load_seeds(conn)
            say("Loaded the reference data every project needs: " +
                ", ".join(f"{k} {v}" for k, v in sorted(loaded.items())))
            available = ", ".join(ref_tables.presets())
            if available:
                say(f"Subject-specific sets are opt-in: "
                    f"`spider ref load --preset {available.split(',')[0]}`")
        for path in args.files or []:
            try:
                kind, count = ref_tables.load_csv(conn, Path(path), args.kind)
            except (ValueError, OSError) as exc:
                conn.close()
                return fail(str(exc))
            say(f"Loaded {count} {kind} row(s) from {path}")
        conn.close()
        return EXIT_OK

    if args.action == "list":
        if args.kind in (None, "counts"):
            available = ", ".join(ref_tables.presets())
            if available:
                say(f"Bundled sets you can load: {available}")
                say("")
            for table_name, count in ref_tables.counts(conn).items():
                say(f"  {table_name:20} {count}")
            conn.close()
            return EXIT_OK
        table_name = {"units": "ref_units", "places": "ref_places",
                      "categories": "ref_vocab", "vocab": "ref_vocab",
                      "aliases": "ref_authority_ids",
                      "sources": "ref_source_rank"}.get(args.kind)
        if not table_name:
            conn.close()
            return fail(f"unknown reference kind '{args.kind}'")
        rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table_name} LIMIT 200")]
        table_print(rows, list(rows[0].keys()) if rows else [])
        conn.close()
        return EXIT_OK

    if args.action == "edit":
        if not args.set:
            conn.close()
            return fail("use: spider ref edit --kind categories --set "
                        "vocabulary=uses,term=medicinal,alias=herbal")
        values = dict(pair.split("=", 1) for pair in args.set.split(",") if "=" in pair)
        table_name, columns = ref_tables.LOADERS[args.kind]
        row = [values.get(c) for c in columns]
        conn.execute(f"INSERT OR REPLACE INTO {table_name}({','.join(columns)}) "
                     f"VALUES({','.join('?' for _ in columns)})", row)
        conn.commit()
        say(f"Updated {table_name}: {values}")
        conn.close()
        return EXIT_OK

    conn.close()
    return fail(f"unknown action '{args.action}'")


# ------------------------------------------------------------------- source
def cmd_source(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    from . import sources as sources_module
    from .spec import SourceItem, guess_source_type

    if args.action == "list":
        rows = sources_module.health(conn)
        table_print(rows, ["id", "type", "location", "tier", "pages_read", "values_accepted"])
        conn.close()
        return EXIT_OK

    if args.action in ("add", "test"):
        if not args.location:
            conn.close()
            return fail("give a link, file or folder to add")
        try:
            spec = load_spec(root, args)
        except SpecError as exc:
            conn.close()
            return fail(str(exc))
        item = SourceItem(
            id=args.id or Path(args.location).stem.replace(" ", "_")[:40] or "source",
            type=guess_source_type(args.location), location=args.location,
            tier=args.tier, language=args.language, pages=args.pages,
            map=dict(pair.split("=", 1) for pair in (args.map or "").split(",")
                     if "=" in pair),
            ai_allowed=not args.no_ai)
        if args.key_env:
            item.key_env = args.key_env
        if item.type == "website":
            sources_module.register(conn, item)
            say(f"Added website source '{item.id}' at tier {item.tier}.")
            say("It will be crawled by `spider crawl`.")
            if args.action == "add":
                _add_to_yaml(spec, item)
                say(f"Written into {spec.path.name} under sources.items.")
            conn.close()
            return EXIT_OK

        result = sources_module.read_source(conn, spec, item, root)
        say(f"{item.id}: {result.note}")
        if result.fields_found:
            say(f"  schema fields found here: {', '.join(result.fields_found)}")
        elif result.readable:
            say("  no schema fields matched by name - add `map:` to say which "
                "column is which field")
        if args.action == "add" and result.readable:
            _add_to_yaml(spec, item)
            say(f"  written into {spec.path.name} under sources.items")
        conn.close()
        return EXIT_OK if result.readable else EXIT_ERROR

    conn.close()
    return fail(f"unknown action '{args.action}'")


def _add_to_yaml(spec, item) -> None:
    entry = {"id": item.id, "type": item.type, "location": item.location,
             "tier": item.tier}
    if item.map:
        entry["map"] = item.map
    if item.pages:
        entry["pages"] = item.pages
    if item.language:
        entry["language"] = item.language
    if not item.ai_allowed:
        entry["ai_allowed"] = False
    items = spec.raw.setdefault("sources", {}).setdefault("items", [])
    items[:] = [i for i in items if not (isinstance(i, dict) and i.get("id") == item.id)]
    items.append(entry)
    spec.save()


# --------------------------------------------------------------- connectors
def cmd_connectors(args) -> int:
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    from . import connectors as connectors_module
    if args.list or not spec.connectors:
        say("Available connectors: " + ", ".join(connectors_module.REGISTRY))
        say("")
        say("Add them to spider.yaml, for example:")
        say("  connectors:")
        say("    - {name: gbif, use: [validate_scientific_name, identifier]}")
        say("    - {name: wikidata, use: [aliases], languages: [en, hi, ta]}")
        say("    - {name: elevation, use: [check_altitude], tolerance_m: 300}")
        if not args.list:
            say("")
            say("None are listed in this project yet.")
        conn.close()
        return EXIT_OK
    records = conn.execute("SELECT COUNT(*) c FROM entities").fetchone()["c"]
    if not records:
        conn.close()
        say("There are no records yet, so there is nothing to ask about.")
        say("Run `spider build` first - it runs the connectors for you as part "
            "of the build, so you rarely need this command at all.")
        return EXIT_OK
    say(f"Running {len(spec.connectors)} connector(s) ...")
    summary = connectors_module.run(conn, spec)
    for item in summary["connectors"]:
        say(f"  {item['name']:10} {item['calls']} calls ({item['cache_hits']} cached), "
            f"{item['values']} values, {item['aliases']} names, "
            f"{item['identifiers']} identifiers, {item['conflicts']} disagreements")
    for skipped in summary["skipped"][:5]:
        say(f"  skipped: {skipped}")
    conn.close()
    return EXIT_OK


# --------------------------------------------------------------- summarise
def cmd_summarise(args) -> int:
    """A short AI summary of each stored page (FR-14)."""
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    from .extract import ai as ai_module
    if not ai_module.available():
        conn.close()
        return fail("summaries need the Claude API - add ANTHROPIC_API_KEY to .env "
                    "and install with pip install 'project-spider[ai]'")

    topic = spec.raw.get("described_as") or (
        f"{', '.join(spec.entities)} - {', '.join(spec.sources.keywords)}")
    where = "" if args.all else "AND (summary IS NULL OR summary = '')"
    pages = conn.execute(
        f"SELECT * FROM pages WHERE relevance > 0 {where} ORDER BY tier, id "
        f"LIMIT ?", (args.limit,)).fetchall()
    if not pages:
        say("Every stored page already has a summary (use --all to redo them).")
        conn.close()
        return EXIT_OK

    say(f"Summarising {len(pages)} page(s) ...")
    done = 0
    for page in pages:
        try:
            summary = ai_module.summarise(conn, page, topic, sentences=args.sentences)
        except ai_module.AIUnavailable as exc:
            conn.close()
            return fail(str(exc))
        except Exception as exc:
            say(f"  skipped {page['url'][:60]}: {type(exc).__name__}")
            continue
        if summary:
            conn.execute("UPDATE pages SET summary=? WHERE id=?", (summary, page["id"]))
            conn.commit()
            done += 1
            say(f"  [{page['id']}] {page['title'][:50]}")
            say(f"      {summary[:200]}")
    conn.close()
    say("")
    say(f"{done} page(s) summarised. They show in `spider get <id>` and the dashboard.")
    return EXIT_OK


# ----------------------------------------------------------------- settings
def cmd_settings(args) -> int:
    """What every setting in spider.yaml means, in plain words."""
    from . import reference

    if args.format == "markdown":
        say(_settings_markdown())
        return EXIT_OK

    if args.key:
        found = reference.find(args.key)
        if not found:
            say(f"Nothing in spider.yaml matches '{args.key}'.")
            say("Try `spider settings` to see them all.")
            return EXIT_ERROR
        for setting in found[:6]:
            _print_setting(setting)
        if len(found) > 6:
            say(f"  ... and {len(found) - 6} more; be more specific to narrow it.")
        return EXIT_OK

    say("Everything you can put in spider.yaml.")
    say("Run `spider settings <name>` for one of them, for example "
        "`spider settings depth`.")
    for section, settings in reference.grouped().items():
        say("")
        say(section.upper())
        for setting in settings:
            head = f"  {setting.key:44}"
            if args.verbose:
                _print_setting(setting)
            else:
                first = setting.what.split(". ")[0].rstrip(".")
                say(f"{head}{first[:62]}")
    say("")
    say("Defaults in force are shown with `spider check`.")
    return EXIT_OK


def _print_setting(setting) -> None:
    say("")
    say(f"  {setting.key}")
    for line in _wrap(setting.what, 72):
        say(f"    {line}")
    bits = [f"type: {setting.kind}"]
    if setting.default:
        bits.append(f"default: {setting.default}")
    if setting.choices:
        bits.append("one of: " + ", ".join(setting.choices))
    say(f"    ({'; '.join(bits)})")
    if setting.example:
        for line in setting.example.splitlines():
            say(f"      {line}")
    if setting.note:
        for line in _wrap(setting.note, 72):
            say(f"    {line}")


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def _settings_markdown() -> str:
    """The same reference as a document, so the two can never disagree."""
    from . import reference

    out = ["# Every setting in `spider.yaml`", "",
           "Generated from `spider/reference.py` by `spider settings "
           "--format markdown`, so it cannot drift from the code.", ""]
    for section, settings in reference.grouped().items():
        out += [f"## {section}", ""]
        for setting in settings:
            out.append(f"### `{setting.key}`")
            out.append("")
            out.append(setting.what)
            out.append("")
            row = [f"**Type** {setting.kind}"]
            if setting.default:
                row.append(f"**Default** `{setting.default}`")
            if setting.choices:
                row.append("**One of** " + ", ".join(f"`{c}`" for c in setting.choices))
            out.append(" &middot; ".join(row))
            out.append("")
            if setting.example:
                out += ["```yaml", setting.example, "```", ""]
            if setting.note:
                out += [setting.note, ""]
    return "\n".join(out)


# -------------------------------------------------------------------- rules
def cmd_rules(args) -> int:
    """Check extraction rules, and re-learn one that stopped working (D6)."""
    root = project_root(args)
    conn = store.connect(root)
    try:
        spec = load_spec(root, args)
    except SpecError as exc:
        conn.close()
        return fail(str(exc))
    from .extract import relearn as relearn_module

    if args.action == "check":
        rules = relearn_module.health(conn, spec)
        if not rules:
            say("No CSS extraction rules in this project - nothing to check.")
            conn.close()
            return EXIT_OK
        say("Extraction rules")
        table_print([{"field": f"{r.entity_type}.{r.field}", "rule": r.rule,
                      "matched": f"{r.pages_matched}/{r.pages_tried}",
                      "state": "BROKEN" if r.dead else "working",
                      "last value": (r.last_seen or "-")[:10]} for r in rules],
                    ["field", "rule", "matched", "state", "last value"])
        broken = [r for r in rules if r.dead]
        if broken:
            say("")
            say(f"{len(broken)} rule(s) match nothing. A site may have changed its "
                f"layout - try `spider rules relearn {broken[0].field}`.")
        conn.close()
        return EXIT_OK

    if args.action == "relearn":
        if not args.field:
            conn.close()
            return fail("say which field: spider rules relearn <field>")
        known = relearn_module.known_values(conn, args.field, args.samples)
        if args.value:
            urls = list(known) or [r["url"] for r in conn.execute(
                "SELECT url FROM pages WHERE relevance > 0 AND url LIKE 'http%' "
                "ORDER BY tier, id LIMIT ?", (args.samples,))]
            known = {url: args.value for url in urls}
        if not known:
            conn.close()
            return fail(
                f"nothing is known about '{args.field}' on any stored page. "
                f"Say what the rule should pick up:\n"
                f"  spider rules relearn {args.field} --value \"the text on the page\"")
        say(f"Re-reading {len(known)} page(s) to see how they look now ...")
        html_by_url = relearn_module.fetch_samples(
            known, delay=spec.sources.delay_seconds,
            on_event=lambda url, why: say(f"  skipped {url[:60]}: {why}"))
        if not html_by_url:
            conn.close()
            return fail("none of the sample pages could be read just now")
        entity_type = next((name for name, ent in spec.entities.items()
                            if args.field in ent.fields), None)
        if entity_type is None:
            conn.close()
            return fail(f"'{args.field}' is not a field in spider.yaml")

        found = relearn_module.relearn(conn, spec, entity_type, args.field,
                                       html_by_url, known)
        if found is None:
            conn.close()
            say(f"Could not find a new selector for '{args.field}' from "
                f"{len(html_by_url)} page(s).")
            say("Give the value that should be picked up, for example:")
            say(f"  spider rules relearn {args.field} --value \"3,000 to 4,500 m\"")
            return EXIT_OK

        say(f"Learned a rule for {found.entity_type}.{found.field}")
        say(f"  was: {found.old_rule or '(none)'}")
        say(f"  now: {found.new_rule}")
        say(f"  works on {found.matched} of {found.tried} sample page(s) "
            f"(confidence {found.confidence})")
        for sample in found.samples:
            say(f"    picks up: {sample}")
        if not args.apply:
            say("")
            say(f"Add it with: spider rules relearn {args.field} --apply")
            conn.close()
            return EXIT_OK
        relearn_module.apply_to_spec(spec, found)
        spec.save()
        say("")
        say(f"Written into {spec.path.name}; the old rule is kept as a fallback.")
        say("Run `spider crawl --refresh` and `spider build` to use it.")
        conn.close()
        return EXIT_OK

    conn.close()
    return fail(f"unknown action '{args.action}'")


# ---------------------------------------------------------------- dashboard
def cmd_dashboard(args) -> int:
    root = project_root(args)
    try:
        import streamlit  # noqa: F401
    except ImportError:
        return fail("the dashboard needs Streamlit "
                    "(pip install 'project-spider[dashboard]')")
    import os
    import subprocess
    app = Path(__file__).parent / "dashboard.py"
    say(f"Starting the dashboard for {root} ...")
    say(f"  http://localhost:{args.port}")
    environment = dict(os.environ)
    environment.setdefault("STREAMLIT_THEME_BASE", "dark")
    environment.setdefault("STREAMLIT_THEME_PRIMARY_COLOR", "#E8382F")
    environment.setdefault("STREAMLIT_THEME_BACKGROUND_COLOR", "#0B0E1A")
    environment.setdefault("STREAMLIT_THEME_SECONDARY_BACKGROUND_COLOR", "#131A2E")
    environment.setdefault("STREAMLIT_THEME_TEXT_COLOR", "#E9EDF8")
    environment.setdefault("STREAMLIT_BROWSER_GATHER_USAGE_STATS", "false")
    command = [sys.executable, "-m", "streamlit", "run", str(app),
               "--server.port", str(args.port)]
    if args.local_only:
        command += ["--server.address", "127.0.0.1"]
    return subprocess.call(command + ["--", "--project", str(root)], env=environment)


# ------------------------------------------------------------------- parser
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="spider",
        description="Describe the dataset you want; Spider collects it from the web "
                    "with a source for every value.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""the seven steps:
  spider init                     create the project and reference databases
  spider describe "..."           draft spider.yaml from a sentence
  spider ref load units.csv       load reference data
  spider check                    validate spider.yaml
  spider crawl                    collect pages
  spider build                    assemble, standardize, merge and derive
  spider report / fill / export   review, fill gaps, take the data""")
    parser.add_argument("--version", action="version", version=f"spider {__version__}")
    parser.add_argument("-p", "--project", help="project folder (default: found upwards)")
    subparsers = parser.add_subparsers(dest="command")

    init = subparsers.add_parser("init", help="create .spider/spider.db here")
    init.add_argument("directory", nargs="?", default=".")
    init.add_argument("--force", action="store_true", help="recreate the database")
    init.add_argument("-q", "--quiet", action="store_true")
    init.set_defaults(func=cmd_init)

    desc = subparsers.add_parser(
        "describe", help="draft spider.yaml from words, an example or a schema")
    desc.add_argument("request", nargs="?", default="",
                      help="what you want, in plain words")
    desc.add_argument("--like", metavar="FILE",
                      help="an example CSV/Excel/JSON whose shape you want")
    desc.add_argument("--from", metavar="FILE",
                      help="an existing structure: .sql, JSON Schema or .db")
    desc.add_argument("--entity", help="name for the entity read from --like")
    desc.add_argument("--ask", action="store_true",
                      help="ask the clarifying questions before writing")
    desc.add_argument("--mode", default="project", choices=["project", "analysis"])
    desc.add_argument("--languages", help="comma separated, for example en,hi,ta")
    desc.add_argument("--seed", action="append", help="a site you already trust")
    desc.add_argument("-o", "--output")
    desc.add_argument("--force", action="store_true", help="overwrite spider.yaml")
    desc.add_argument("--no-ai", action="store_true")
    desc.set_defaults(func=cmd_describe)

    check = subparsers.add_parser("check", help="validate spider.yaml")
    check.add_argument("-f", "--file")
    check.set_defaults(func=cmd_check)

    crawl = subparsers.add_parser("crawl", help="collect pages")
    crawl.add_argument("urls", nargs="*", help="seed URLs (default: from spider.yaml)")
    crawl.add_argument("-k", "--keyword", action="append", help="keyword filter")
    crawl.add_argument("-d", "--depth", type=int, default=2)
    crawl.add_argument("-n", "--max-pages", type=int, default=None)
    crawl.add_argument("--delay", type=float, default=1.0, help="seconds per domain")
    crawl.add_argument("-e", "--extract", action="append", metavar="NAME=CSS")
    crawl.add_argument("--any-domain", action="store_true")
    crawl.add_argument("--domains", help="comma separated allow list")
    crawl.add_argument("--refresh", action="store_true", help="re-fetch stored pages")
    crawl.add_argument("--ignore-robots", action="store_true",
                       help="only for sites you own")
    crawl.add_argument("--ai-relevance", action="store_true",
                       help="ask the model whether each page holds schema facts")
    crawl.add_argument("-f", "--file")
    crawl.add_argument("-v", "--verbose", action="store_true")
    crawl.set_defaults(func=cmd_crawl)

    search = subparsers.add_parser("search", help="ranked full-text search")
    search.add_argument("query")
    search.add_argument("-n", "--limit", type=int, default=10)
    search.add_argument("--domain")
    search.set_defaults(func=cmd_search)

    get = subparsers.add_parser("get", help="show one stored page")
    get.add_argument("page_id", type=int)
    get.add_argument("-c", "--chars", type=int, default=1500)
    get.add_argument("--json", action="store_true")
    get.set_defaults(func=cmd_get)

    stats = subparsers.add_parser("stats", help="summary of what is stored")
    stats.set_defaults(func=cmd_stats)

    sql = subparsers.add_parser("sql", help="run a read-only SELECT")
    sql.add_argument("query")
    sql.add_argument("-f", "--format", default="table",
                     choices=["table", "json", "csv"])
    sql.set_defaults(func=cmd_sql)

    preview = subparsers.add_parser(
        "preview", help="try the whole chain on a few pages before a full crawl")
    preview.add_argument("-n", "--pages", type=int, default=4,
                         help="how many pages to read (default 4)")
    preview.add_argument("-r", "--rows", type=int, default=5,
                         help="how many rows to show")
    preview.add_argument("--no-ai", action="store_true")
    preview.add_argument("-f", "--file")
    preview.set_defaults(func=cmd_preview)

    build = subparsers.add_parser("build", help="assemble the dataset from pages")
    build.add_argument("--normalize", help="3NF, BCNF, 4NF, 5NF, 6NF (or 0NF-2NF in "
                                           "analysis mode)")
    build.add_argument("--mode", choices=["project", "analysis"])
    build.add_argument("--limit", type=int, help="read only this many pages")
    build.add_argument("--no-ai", action="store_true", help="rules only")
    build.add_argument("--no-connectors", action="store_true",
                       help="skip the services listed in spider.yaml")
    build.add_argument("--no-export", action="store_true")
    build.add_argument("-f", "--file")
    build.set_defaults(func=cmd_build)

    report = subparsers.add_parser("report", help="coverage, conflicts, gaps, sources")
    report.add_argument("--gold", help="CSV of facts you are sure about")
    report.add_argument("-n", "--limit", type=int, default=10)
    report.add_argument("-f", "--file")
    report.set_defaults(func=cmd_report)

    fill = subparsers.add_parser("fill", help="crawl for the empty cells only")
    fill.add_argument("-n", "--max-pages", type=int, default=40)
    fill.add_argument("-q", "--queries", type=int, default=10)
    fill.add_argument("--until-stable", action="store_true",
                      help="repeat until coverage stops improving (D3)")
    fill.add_argument("--rounds", type=int, default=5,
                      help="most rounds to run with --until-stable")
    fill.add_argument("-v", "--verbose", action="store_true")
    fill.add_argument("-f", "--file")
    fill.set_defaults(func=cmd_fill)

    derive = subparsers.add_parser("derive", help="review calculated fields")
    derive.add_argument("action", choices=["list", "approve", "edit", "reject", "run"],
                        nargs="?", default="list")
    derive.add_argument("name", nargs="?")
    derive.add_argument("--formula")
    derive.add_argument("-f", "--file")
    derive.set_defaults(func=cmd_derive)

    explain = subparsers.add_parser("explain", help="trace a value to its sources")
    explain.add_argument("entity")
    explain.add_argument("field")
    explain.add_argument("-f", "--file")
    explain.set_defaults(func=cmd_explain)

    review = subparsers.add_parser(
        "review", help="decide what the build flagged: conflicts, low confidence")
    review.add_argument("action", choices=["list", "show", "keep", "reject",
                                           "ask", "forget"],
                        nargs="?", default="list")
    review.add_argument("item_id", nargs="?",
                        help="the [id] shown by `spider review`, or with forget, "
                             "the target such as plant.altitude_m")
    review.add_argument("choice", nargs="?",
                        help="with keep: the option number (default 1) or 'all'; "
                             "for a low-confidence value: 'keep'")
    review.add_argument("-n", "--limit", type=int, default=25)
    review.add_argument("-f", "--file")
    review.set_defaults(func=cmd_review)

    export = subparsers.add_parser("export", help="write the dataset out")
    export.add_argument("-t", "--target", help="one target from output.targets")
    export.add_argument("-f", "--format", choices=["csv", "json", "sqlite", "xlsx", "sql"])
    export.add_argument("--pages", action="store_true",
                        help="export the raw pages instead of the dataset")
    export.add_argument("--pack", action="store_true",
                        help="one portable file with the dataset and its evidence")
    export.add_argument("--with-pages", action="store_true",
                        help="with --pack, include the collected pages too")
    export.add_argument("-o", "--output", help="where to write the pack")
    export.set_defaults(func=cmd_export)

    ref = subparsers.add_parser("ref", help="reference tables")
    ref.add_argument("action", choices=["load", "list", "edit"])
    ref.add_argument("files", nargs="*")
    ref.add_argument("--kind", choices=["units", "places", "categories", "vocab",
                                        "aliases", "sources", "counts"])
    ref.add_argument("--preset", help="a bundled reference set, by name")
    ref.add_argument("--set")
    ref.set_defaults(func=cmd_ref)

    source = subparsers.add_parser("source", help="your own links, files and folders")
    source.add_argument("action", choices=["add", "test", "list"])
    source.add_argument("location", nargs="?")
    source.add_argument("--id")
    source.add_argument("--tier", type=int, default=0,
                        help="0 = you vouch for it, 1 official, 2 established, 3 other")
    source.add_argument("--map", help='column=field pairs, "Species=scientific_name,..."')
    source.add_argument("--pages", help="PDF page range, for example 12-40")
    source.add_argument("--language")
    source.add_argument("--key-env", metavar="VAR",
                        help="environment variable holding this API's key")
    source.add_argument("--no-ai", action="store_true",
                        help="never send this source's text to the AI")
    source.add_argument("-f", "--file")
    source.set_defaults(func=cmd_source)

    connectors = subparsers.add_parser("connectors", help="run the listed connectors")
    connectors.add_argument("--list", action="store_true")
    connectors.add_argument("-f", "--file")
    connectors.set_defaults(func=cmd_connectors)

    summarise = subparsers.add_parser(
        "summarise", help="write a short AI summary of each stored page")
    summarise.add_argument("-n", "--limit", type=int, default=25)
    summarise.add_argument("--sentences", type=int, default=3)
    summarise.add_argument("--all", action="store_true",
                           help="redo pages that already have one")
    summarise.add_argument("-f", "--file")
    summarise.set_defaults(func=cmd_summarise)

    settings = subparsers.add_parser(
        "settings", help="what every setting in spider.yaml means")
    settings.add_argument("key", nargs="?",
                          help="a setting, or part of one: depth, tier, seeds")
    settings.add_argument("-v", "--verbose", action="store_true",
                          help="full entry for every setting")
    settings.add_argument("--format", default="text", choices=["text", "markdown"])
    settings.set_defaults(func=cmd_settings)

    rules = subparsers.add_parser(
        "rules", help="check extraction rules, or re-learn a broken one")
    rules.add_argument("action", choices=["check", "relearn"], nargs="?",
                       default="check")
    rules.add_argument("field", nargs="?")
    rules.add_argument("--value", help="the value the rule should pick up")
    rules.add_argument("--samples", type=int, default=5)
    rules.add_argument("--apply", action="store_true",
                       help="write the new rule into spider.yaml")
    rules.add_argument("-f", "--file")
    rules.set_defaults(func=cmd_rules)

    dashboard = subparsers.add_parser("dashboard", help="the local web dashboard")
    dashboard.add_argument("--port", type=int, default=8501)
    dashboard.add_argument("--local-only", action="store_true",
                           help="listen on 127.0.0.1 only, not the local network")
    dashboard.set_defaults(func=cmd_dashboard)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return EXIT_OK
    try:
        if args.command not in ("init", "settings"):
            root = project_root(args)
            from .extract.ai import load_dotenv
            load_dotenv(root)
        return args.func(args)
    except store.ProjectNotFound as exc:
        return fail(str(exc))
    except SpecError as exc:
        return fail(str(exc))
    except KeyboardInterrupt:
        say("\nStopped. Everything collected so far is saved.")
        return EXIT_OK
    except BrokenPipeError:
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
