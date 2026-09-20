"""Work through the review queue (section 13, stage 4).

The build flags conflicts and doubtful values instead of hiding them; this
module is what lets a person decide. Every decision is recorded in
`review_decisions`, so a later `spider build` - which recomputes everything
from the pages - re-applies it instead of asking again.

Nothing happens silently: a decision can be seen with `spider explain`, and
a conflict that new evidence reopens after a fill crawl is queued again.
"""

from __future__ import annotations

from .store.db import jdump, jload, now


def open_items(conn, kind: str | None = None, limit: int = 100) -> list[dict]:
    sql = "SELECT * FROM review_queue WHERE status='open'"
    params: list = []
    if kind:
        sql += " AND kind=?"
        params.append(kind)
    sql += " ORDER BY id LIMIT ?"
    params.append(limit)
    out = []
    for row in conn.execute(sql, params):
        item = dict(row)
        item["detail"] = jload(item.get("detail"), {})
        out.append(item)
    return out


def get_item(conn, item_id: int) -> dict | None:
    row = conn.execute("SELECT * FROM review_queue WHERE id=?", (item_id,)).fetchone()
    if not row:
        return None
    item = dict(row)
    item["detail"] = jload(item.get("detail"), {})
    return item


# ------------------------------------------------------------------ decisions
def keep(conn, item_id: int, choice=1) -> dict:
    """Keep one option of a conflict ('all' keeps every value), or promote a
    low-confidence value the user is happy with."""
    item = get_item(conn, item_id)
    if not item:
        raise KeyError(f"no review item {item_id}")
    if item["kind"] == "low_confidence":
        if str(choice) not in ("keep", "1"):
            raise ValueError("a low-confidence value has one option: "
                             "keep it, or reject it")
        _decide(conn, item, "keep", supersede_others=False,
                note="kept by the user despite low confidence")
        conn.execute("UPDATE review_queue SET status='resolved' WHERE id=?",
                     (item_id,))
        conn.commit()
        return {"item": item, "kept": item["detail"].get("value"),
                "note": "the value stays in the dataset, approved by you"}
    options = item["detail"].get("options") or []
    if str(choice).lower() == "all":
        _record(conn, item, "all")
        conn.execute("UPDATE review_queue SET status='resolved' WHERE id=?",
                     (item_id,))
        conn.commit()
        return {"item": item, "kept": "all",
                "note": f"all {len(options)} value(s) stay in the dataset, still flagged"}
    if isinstance(choice, str) and choice.isdigit():
        choice = int(choice)
    if not isinstance(choice, int) or not 1 <= choice <= len(options):
        raise ValueError(
            f"'{item['target']}' has {len(options)} option(s); "
            f"keep 1-{len(options)} or 'all'")
    chosen = options[choice - 1]
    _decide(conn, item, chosen.get("value"), supersede_others=True,
            note=f"user kept this over {len(options) - 1} other value(s)")
    conn.execute("UPDATE review_queue SET status='resolved' WHERE id=?", (item_id,))
    _settle(conn, item, keep_all=False)
    conn.commit()
    return {"item": item, "kept": chosen.get("value"),
            "note": (f"kept {chosen.get('value')}; the other value(s) stay stored "
                     f"but marked superseded")}


def reject(conn, item_id: int) -> dict:
    """Take the doubtful value out of the dataset (it stays stored as evidence)."""
    item = get_item(conn, item_id)
    if not item:
        raise KeyError(f"no review item {item_id}")
    if item["entity_id"] is None:
        raise ValueError("this item has no value behind it to reject")
    _decide(conn, item, "reject", supersede_others=True,
            note="rejected by the user in review")
    conn.execute("UPDATE review_queue SET status='resolved' WHERE id=?", (item_id,))
    _settle(conn, item, keep_all=False)
    conn.commit()
    return {"item": item, "kept": None,
            "note": "the value left the dataset; its row is kept as superseded evidence"}


def _settle(conn, item, keep_all: bool) -> None:
    """A decided cell leaves its sibling questions moot.

    Once the losing value is superseded, the low-confidence item that asked
    about it no longer means anything. With 'keep all' every value stays in,
    so those questions keep their place in the queue.
    """
    if keep_all:
        return
    conn.execute(
        "UPDATE review_queue SET status='resolved' "
        "WHERE status='open' AND target=? AND entity_id IS ? "
        "AND kind='low_confidence'", (item["target"], item["entity_id"]))


def _decide(conn, item, choice, supersede_others: bool, note: str) -> None:
    entity_id, field = item["entity_id"], item["field"]
    rows = conn.execute(
        "SELECT id, value, status, lineage FROM attributes "
        "WHERE entity_id=? AND name=? AND status IN ('accepted','review')",
        (entity_id, field)).fetchall()
    for row in rows:
        keep_row = (not supersede_others) or str(row["value"]) == str(choice)
        # an explicit human decision is the approval the confidence floor
        # was waiting for, so a kept value enters the dataset for good
        status = "accepted" if keep_row else "superseded"
        lineage = jload(row["lineage"], {}) or {}
        lineage["user_decision"] = {"choice": str(choice), "note": note,
                                    "at": now()} if keep_row else \
            {"choice": "another value", "note": note, "at": now()}
        conn.execute("UPDATE attributes SET status=?, lineage=? WHERE id=?",
                     (status, jdump(lineage), row["id"]))
    _record(conn, item, str(choice) if supersede_others else "keep")
    conn.commit()


def _record(conn, item, choice: str) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO review_decisions(target,kind,choice,decided_at) "
        "VALUES(?,?,?,?)", (item["target"], item["kind"], choice, now()))


def forget(conn, target: str) -> int:
    """Drop a stored decision, so the next build asks the question again."""
    cursor = conn.execute("DELETE FROM review_decisions WHERE target=?", (target,))
    conn.commit()
    return cursor.rowcount


def reapply(conn) -> int:
    """After a rebuild, honour the decisions already made.

    The public keep and reject do the work, so a re-applied decision looks
    exactly like the original one. A conflict whose sources now agree needs
    no decision any more, so its recorded one is dropped. An 'ask for more
    sources' request is never re-applied: fresh evidence should reopen the
    question, not close it.
    """
    applied = 0
    rows = conn.execute("SELECT * FROM review_decisions").fetchall()
    for row in rows:
        item = conn.execute(
            "SELECT * FROM review_queue WHERE kind=? AND target=? AND status='open' "
            "ORDER BY id DESC LIMIT 1", (row["kind"], row["target"])).fetchone()
        if not item:
            conn.execute("DELETE FROM review_decisions WHERE target=? AND kind=?",
                         (row["target"], row["kind"]))
            continue
        try:
            if row["kind"] == "conflict":
                if row["choice"] == "all":
                    keep(conn, item["id"], "all")
                elif row["choice"] == "reject":
                    reject(conn, item["id"])
                else:
                    options = (jload(item["detail"], {}) or {}).get("options") or []
                    index = next((i + 1 for i, option in enumerate(options)
                                  if str(option.get("value")) == str(row["choice"])),
                                 None)
                    if not index:
                        continue
                    keep(conn, item["id"], index)
            elif row["kind"] == "low_confidence":
                if str(row["choice"]) == "keep":
                    keep(conn, item["id"], "keep")
                else:
                    reject(conn, item["id"])
            else:
                continue
            applied += 1
        except (KeyError, ValueError):
            continue
    conn.commit()
    return applied


# ------------------------------------------------------- ask for more sources
def ask_more(conn, spec, item_id: int) -> dict:
    """'Ask for more sources' (screen 4): search for pages that state this
    cell, crawl them, and let the next build settle the disagreement."""
    item = get_item(conn, item_id)
    if not item:
        raise KeyError(f"no review item {item_id}")
    if not item["entity_id"]:
        raise ValueError("this item has no record behind it to look for")

    name = conn.execute("SELECT canonical_name FROM entities WHERE id=?",
                        (item["entity_id"],)).fetchone()
    if not name:
        raise ValueError("the record behind this item no longer exists")
    readable = (item["field"] or "value").replace("_", " ")
    described = spec.raw.get("described_as") or ""
    context = " ".join(str(described).split()[:4])
    queries = [q for q in (" ".join(filter(None, [name["canonical_name"], readable,
                                                  context])),)
               if q.strip()]

    from .crawl.discovery import discover
    urls, note = discover(conn, spec, queries, limit=15)
    crawled = 0
    if urls:
        from .crawl.crawler import crawl_from_spec
        result = crawl_from_spec(conn, spec, seeds=urls, max_pages=15, depth=1,
                                 kind="fill")
        crawled = result.saved
    conn.execute(
        "UPDATE review_queue SET detail=? WHERE id=?",
        (jdump({**item["detail"], "asked_at": now(),
                "asked_for": f"{len(urls)} url(s), {crawled} saved"}), item_id))
    conn.commit()
    return {"item": item, "queries": queries, "found": len(urls),
            "crawled": crawled, "note": note}
