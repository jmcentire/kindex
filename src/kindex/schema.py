"""SQLite schema for the Kindex knowledge graph."""

from __future__ import annotations

SCHEMA_VERSION = 2

# Audience scopes for tenancy model
AUDIENCES = ("private", "team", "public")

# Knowledge node types
NODE_TYPES = (
    "concept", "document", "session", "person", "project",
    "decision", "question", "artifact", "skill",
)

# Operational node types — what must hold, what to verify, what to watch
OPERATIONAL_TYPES = (
    "constraint",   # invariants that must hold (hard rules)
    "directive",    # behavioral rules, style guides (soft rules with context)
    "checkpoint",   # things to verify before an event (pre-flight lists)
    "watch",        # open questions, known instabilities (decaying attention flags)
)

ALL_NODE_TYPES = NODE_TYPES + OPERATIONAL_TYPES

# Edge types — bidirectional by convention
EDGE_TYPES = (
    "relates_to", "answers", "contradicts", "implements", "depends_on",
    "spawned_from", "supersedes", "exemplifies", "context_of",
)

CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL DEFAULT 'concept',
    title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL DEFAULT '',
    aka TEXT NOT NULL DEFAULT '',           -- JSON array of synonyms
    intent TEXT NOT NULL DEFAULT '',        -- "I was trying to..."
    -- provenance
    prov_who TEXT NOT NULL DEFAULT '',      -- JSON array of person IDs
    prov_when TEXT NOT NULL DEFAULT '',     -- ISO datetime
    prov_activity TEXT NOT NULL DEFAULT '', -- meeting / debug-session / etc.
    prov_why TEXT NOT NULL DEFAULT '',      -- what question prompted capture
    prov_source TEXT NOT NULL DEFAULT '',   -- url / file path / session id
    -- scoring
    weight REAL NOT NULL DEFAULT 0.5,
    domains TEXT NOT NULL DEFAULT '',       -- JSON array
    status TEXT NOT NULL DEFAULT 'active',  -- active / archived / deprecated / open-question
    audience TEXT NOT NULL DEFAULT 'private',  -- private / team / public
    -- timestamps
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    last_accessed TEXT NOT NULL DEFAULT (datetime('now')),
    -- extra fields as JSON (preserves domain-specific data)
    extra TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS edges (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL REFERENCES nodes(id),
    to_id TEXT NOT NULL REFERENCES nodes(id),
    type TEXT NOT NULL DEFAULT 'relates_to',
    weight REAL NOT NULL DEFAULT 0.5,
    provenance TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(from_id, to_id, type)
);

-- FTS5 full-text search over node content
CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id UNINDEXED,
    title,
    content,
    aka,
    intent,
    domains,
    content=nodes,
    content_rowid=rowid
);

-- Triggers to keep FTS in sync
CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, title, content, aka, intent, domains)
    VALUES (new.rowid, new.id, new.title, new.content, new.aka, new.intent, new.domains);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, title, content, aka, intent, domains)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.aka, old.intent, old.domains);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, title, content, aka, intent, domains)
    VALUES ('delete', old.rowid, old.id, old.title, old.content, old.aka, old.intent, old.domains);
    INSERT INTO nodes_fts(rowid, id, title, content, aka, intent, domains)
    VALUES (new.rowid, new.id, new.title, new.content, new.aka, new.intent, new.domains);
END;

-- Indexes
CREATE INDEX IF NOT EXISTS idx_edges_from ON edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON edges(to_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_nodes_status ON nodes(status);
CREATE INDEX IF NOT EXISTS idx_nodes_updated ON nodes(updated_at);
CREATE INDEX IF NOT EXISTS idx_nodes_weight ON nodes(weight DESC);
CREATE INDEX IF NOT EXISTS idx_nodes_audience ON nodes(audience);

-- Schema version tracking
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
"""
