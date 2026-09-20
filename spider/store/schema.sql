-- Project Spider schema (design document section 7).
-- Pages are the evidence layer; entities/attributes/relations are the
-- canonical 6NF dataset built on top of them.

PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

-- ---------------------------------------------------------------- crawl layer
CREATE TABLE IF NOT EXISTS crawls (
  id          INTEGER PRIMARY KEY,
  started_at  TEXT NOT NULL,
  finished_at TEXT,
  seeds       TEXT,
  keywords    TEXT,
  depth       INTEGER,
  max_pages   INTEGER,
  pages_saved INTEGER DEFAULT 0,
  pages_seen  INTEGER DEFAULT 0,
  kind        TEXT DEFAULT 'crawl'        -- crawl | fill | preview | source
);

CREATE TABLE IF NOT EXISTS pages (
  id           INTEGER PRIMARY KEY,
  crawl_id     INTEGER REFERENCES crawls(id),
  url          TEXT UNIQUE NOT NULL,
  domain       TEXT,
  title        TEXT,
  description  TEXT,
  author       TEXT,
  published    TEXT,
  lang         TEXT,
  headings     TEXT,
  text         TEXT,
  word_count   INTEGER,
  relevance    REAL,
  status       INTEGER,
  content_hash TEXT,
  depth        INTEGER,
  tier         INTEGER DEFAULT 3,
  source_id    TEXT,
  summary      TEXT,
  fetched_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pages_domain ON pages(domain);
CREATE INDEX IF NOT EXISTS idx_pages_hash   ON pages(content_hash);

CREATE TABLE IF NOT EXISTS links (
  from_page INTEGER REFERENCES pages(id),
  to_url    TEXT,
  PRIMARY KEY (from_page, to_url)
);

CREATE TABLE IF NOT EXISTS fields (
  page_id INTEGER REFERENCES pages(id),
  name    TEXT,
  value   TEXT
);
CREATE INDEX IF NOT EXISTS idx_fields_name ON fields(name);

CREATE TABLE IF NOT EXISTS structured (
  page_id INTEGER REFERENCES pages(id),
  type    TEXT,
  data    TEXT
);

-- not an external-content table: FTS5 keeps its own copy so snippet() can
-- return highlighted text (FR-9)
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts USING fts5(
  title, description, headings, text
);

-- ------------------------------------------------------------ dataset layer
CREATE TABLE IF NOT EXISTS entities (
  id             INTEGER PRIMARY KEY,
  type           TEXT NOT NULL,
  canonical_name TEXT,
  identity_key   TEXT,
  created_at     TEXT
);
CREATE INDEX IF NOT EXISTS idx_entities_key ON entities(type, identity_key);

CREATE TABLE IF NOT EXISTS entity_aliases (
  entity_id INTEGER REFERENCES entities(id),
  alias     TEXT,
  language  TEXT,
  script    TEXT,
  source    TEXT,
  PRIMARY KEY (entity_id, alias, language)
);
CREATE INDEX IF NOT EXISTS idx_alias_alias ON entity_aliases(alias);

-- One value per row: the canonical 6NF store (section 7, D8).
CREATE TABLE IF NOT EXISTS attributes (
  id          INTEGER PRIMARY KEY,
  entity_id   INTEGER REFERENCES entities(id),
  name        TEXT NOT NULL,
  value       TEXT,
  value_num   REAL,
  value_max   REAL,                        -- ranges keep min in value_num
  unit        TEXT,
  raw_value   TEXT,                        -- text exactly as found
  origin      TEXT DEFAULT 'extracted',    -- extracted | derived | inferred
  source_page INTEGER REFERENCES pages(id),
  source_id   TEXT,
  evidence    TEXT,                        -- quote, or sheet!cell / p.12
  tier        INTEGER,
  confidence  REAL,
  status      TEXT DEFAULT 'accepted',     -- accepted | review | rejected | superseded
  conflict_id INTEGER,
  lineage     TEXT,                        -- json: formula + input attribute ids
  valid_from  TEXT,
  valid_to    TEXT,
  created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_attr_entity ON attributes(entity_id, name);
CREATE INDEX IF NOT EXISTS idx_attr_status ON attributes(status);

CREATE TABLE IF NOT EXISTS relations (
  id          INTEGER PRIMARY KEY,
  from_entity INTEGER REFERENCES entities(id),
  relation    TEXT,
  to_entity   INTEGER REFERENCES entities(id),
  source_page INTEGER REFERENCES pages(id),
  evidence    TEXT,
  confidence  REAL,
  created_at  TEXT,
  UNIQUE (from_entity, relation, to_entity, source_page)
);

CREATE TABLE IF NOT EXISTS derivations (
  name         TEXT,
  entity_type  TEXT,
  method       TEXT,
  inputs       TEXT,
  formula      TEXT,
  if_missing   TEXT,
  unit         TEXT,
  explain      TEXT,
  status       TEXT DEFAULT 'active',   -- active | suggested | rejected
  suggested_by TEXT,
  approved_on  TEXT,
  fills        INTEGER DEFAULT 0,
  samples      TEXT,
  PRIMARY KEY (entity_type, name)
);

CREATE TABLE IF NOT EXISTS review_queue (
  id         INTEGER PRIMARY KEY,
  kind       TEXT,      -- conflict | low_confidence | strict_reject | suggestion | sanity
  target     TEXT,      -- entity/field or derivation name
  entity_id  INTEGER,
  field      TEXT,
  reason     TEXT,
  detail     TEXT,      -- json
  status     TEXT DEFAULT 'open',  -- open | resolved | dismissed
  created_at TEXT
);

-- Decisions made in `spider review` (section 13, stage 4). A build
-- recomputes the queue from the pages, so it re-applies these instead of
-- asking the same question twice.
CREATE TABLE IF NOT EXISTS review_decisions (
  target     TEXT,
  kind       TEXT,      -- conflict | low_confidence
  choice     TEXT,      -- the kept value, or 'all', or 'reject', or 'keep'
  decided_at TEXT,
  PRIMARY KEY (target, kind)
);

CREATE TABLE IF NOT EXISTS build_reports (
  id          INTEGER PRIMARY KEY,
  built_at    TEXT,
  mode        TEXT,
  normal_form TEXT,
  passed      INTEGER,
  changes     TEXT       -- json standardization report
);

CREATE TABLE IF NOT EXISTS sources (
  id           TEXT PRIMARY KEY,
  type         TEXT,       -- website | file | pdf | csv | xlsx | folder | connector
  location     TEXT,
  tier         INTEGER DEFAULT 3,
  authoritative_for TEXT,
  config       TEXT,
  added_at     TEXT,
  pages_read   INTEGER DEFAULT 0,
  values_given INTEGER DEFAULT 0,
  values_rejected INTEGER DEFAULT 0,
  values_conflict INTEGER DEFAULT 0
);

-- ------------------------------------------------------------ reference data
CREATE TABLE IF NOT EXISTS ref_units (
  quantity TEXT, unit TEXT, base_unit TEXT, factor REAL, offset REAL DEFAULT 0,
  PRIMARY KEY (quantity, unit)
);
CREATE TABLE IF NOT EXISTS ref_places (
  code TEXT PRIMARY KEY, name TEXT, level TEXT, parent TEXT, aliases TEXT, lat REAL, lon REAL
);
CREATE TABLE IF NOT EXISTS ref_vocab (
  vocabulary TEXT, term TEXT, alias TEXT,
  PRIMARY KEY (vocabulary, alias)
);
CREATE TABLE IF NOT EXISTS ref_authority_ids (
  entity_type TEXT, authority TEXT, name TEXT, identifier TEXT,
  PRIMARY KEY (authority, name)
);
CREATE TABLE IF NOT EXISTS ref_source_rank (
  domain TEXT PRIMARY KEY, tier INTEGER, note TEXT
);

-- ----------------------------------------------------------------- AI / API
CREATE TABLE IF NOT EXISTS ai_cache (
  key        TEXT PRIMARY KEY,
  kind       TEXT,
  response   TEXT,
  created_at TEXT
);

-- what each page said last time, kept across rebuilds so a rule that stops
-- matching can be re-learned from the value it used to give (D6)
CREATE TABLE IF NOT EXISTS value_history (
  url       TEXT,
  field     TEXT,
  value     TEXT,
  raw_value TEXT,
  seen_at   TEXT,
  PRIMARY KEY (url, field)
);

CREATE TABLE IF NOT EXISTS meta (
  key TEXT PRIMARY KEY, value TEXT
);
