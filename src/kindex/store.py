"""SQLite store — primary persistence layer for Kindex."""

from __future__ import annotations

import datetime as _dt
import json
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def _json_default(obj):
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def _jdumps(obj):
    return json.dumps(obj, default=_json_default)

from .config import Config
from .schema import CREATE_TABLES, SCHEMA_VERSION


def _now() -> str:
    return datetime.now(tz=None).isoformat(timespec="seconds")


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


class Store:
    """SQLite-backed knowledge graph with FTS5 full-text search.

    This is the primary query engine. Markdown files remain as
    human-readable canonical source; the store indexes them.
    """

    def __init__(self, config: Config):
        self.config = config
                # Support both kindex.db (new) and conv.db (legacy)
        new_db = config.data_path / "kindex.db"
        old_db = config.data_path / "conv.db"
        self.db_path = old_db if old_db.exists() and not new_db.exists() else new_db
        self._conn: sqlite3.Connection | None = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.config.data_path.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
        return self._conn

    def _init_schema(self) -> None:
        self.conn.executescript(CREATE_TABLES)
        # Set schema version if not present
        cur = self.conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        row = cur.fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()
        else:
            self._migrate_schema(int(row["value"]))

    def _migrate_schema(self, current_version: int) -> None:
        """Apply incremental schema migrations."""
        if current_version < 2:
            # v2: add audience column
            try:
                self.conn.execute("ALTER TABLE nodes ADD COLUMN audience TEXT NOT NULL DEFAULT 'private'")
                self.conn.execute("CREATE INDEX IF NOT EXISTS idx_nodes_audience ON nodes(audience)")
                self.conn.commit()
            except Exception:
                pass  # column already exists

        if current_version < 3:
            # v3: add activity_log table
            try:
                self.conn.executescript("""
                    CREATE TABLE IF NOT EXISTS activity_log (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                        action TEXT NOT NULL,
                        target_id TEXT NOT NULL DEFAULT '',
                        target_title TEXT NOT NULL DEFAULT '',
                        actor TEXT NOT NULL DEFAULT '',
                        details TEXT NOT NULL DEFAULT ''
                    );
                    CREATE INDEX IF NOT EXISTS idx_activity_timestamp ON activity_log(timestamp);
                    CREATE INDEX IF NOT EXISTS idx_activity_action ON activity_log(action);
                """)
                self.conn.commit()
            except Exception:
                pass

        if current_version < 4:
            # v4: add suggestions table
            try:
                self.conn.executescript("""
                    CREATE TABLE IF NOT EXISTS suggestions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        concept_a TEXT NOT NULL,
                        concept_b TEXT NOT NULL,
                        reason TEXT NOT NULL DEFAULT '',
                        source TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'pending',
                        created_at TEXT NOT NULL DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_suggestions_status ON suggestions(status);
                """)
                self.conn.commit()
            except Exception:
                pass

        if current_version < SCHEMA_VERSION:
            self.conn.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            self.conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ── Activity logging ─────────────────────────────────────────────

    def _log(self, action: str, target_id: str = "", target_title: str = "",
             actor: str = "", details: dict | None = None) -> None:
        """Record an action in the activity log."""
        try:
            self.conn.execute(
                """INSERT INTO activity_log (action, target_id, target_title, actor, details)
                   VALUES (?, ?, ?, ?, ?)""",
                (action, target_id, target_title, actor,
                 _jdumps(details or {})),
            )
            self.conn.commit()
        except Exception:
            pass  # don't let logging break operations

    def recent_activity(self, limit: int = 50) -> list[dict]:
        """Get recent activity log entries."""
        try:
            rows = self.conn.execute(
                "SELECT * FROM activity_log ORDER BY timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                result.append(d)
            return result
        except Exception:
            return []

    # ── Temporal queries ───────────────────────────────────────────────

    def activity_since(self, since_iso: str, action: str | None = None) -> list[dict]:
        """Get activity log entries since a timestamp, optionally filtered by action type."""
        try:
            q = "SELECT * FROM activity_log WHERE timestamp >= ? "
            params: list = [since_iso]
            if action:
                q += "AND action = ? "
                params.append(action)
            q += "ORDER BY timestamp DESC"
            rows = self.conn.execute(q, params).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                result.append(d)
            return result
        except Exception:
            return []

    def nodes_changed_since(self, since_iso: str) -> list[dict]:
        """Get nodes that were updated since a timestamp."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE updated_at >= ? ORDER BY updated_at DESC",
            (since_iso,),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def activity_by_actor(self, actor: str, limit: int = 50) -> list[dict]:
        """Get activity by a specific actor."""
        try:
            rows = self.conn.execute(
                "SELECT * FROM activity_log WHERE actor = ? ORDER BY timestamp DESC LIMIT ?",
                (actor, limit),
            ).fetchall()
            result = []
            for r in rows:
                d = dict(r)
                if isinstance(d.get("details"), str):
                    try:
                        d["details"] = json.loads(d["details"])
                    except (json.JSONDecodeError, TypeError):
                        pass
                result.append(d)
            return result
        except Exception:
            return []

    # ── Suggestions ───────────────────────────────────────────────────

    def add_suggestion(self, concept_a: str, concept_b: str,
                       reason: str = "", source: str = "") -> int:
        """Add a bridge opportunity suggestion. Returns the suggestion ID."""
        cur = self.conn.execute(
            """INSERT INTO suggestions (concept_a, concept_b, reason, source)
               VALUES (?, ?, ?, ?)""",
            (concept_a, concept_b, reason, source),
        )
        self.conn.commit()
        self._log("add_suggestion", f"{concept_a}->{concept_b}", "",
                  details={"reason": reason, "source": source})
        return cur.lastrowid

    def pending_suggestions(self, limit: int = 20) -> list[dict]:
        """Get pending suggestions (bridge opportunities)."""
        try:
            rows = self.conn.execute(
                "SELECT * FROM suggestions WHERE status = 'pending' "
                "ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
            return [dict(r) for r in rows]
        except Exception:
            return []

    def update_suggestion(self, suggestion_id: int, status: str) -> None:
        """Update suggestion status (accepted/rejected)."""
        self.conn.execute(
            "UPDATE suggestions SET status = ? WHERE id = ?",
            (status, suggestion_id),
        )
        self.conn.commit()
        self._log("update_suggestion", str(suggestion_id), "",
                  details={"status": status})

    # ── Node operations ────────────────────────────────────────────────

    def add_node(
        self,
        title: str,
        content: str = "",
        *,
        node_id: str | None = None,
        node_type: str = "concept",
        aka: list[str] | None = None,
        intent: str = "",
        domains: list[str] | None = None,
        status: str = "active",
        audience: str = "private",
        weight: float = 0.5,
        prov_who: list[str] | None = None,
        prov_when: str | None = None,
        prov_activity: str = "",
        prov_why: str = "",
        prov_source: str = "",
        extra: dict | None = None,
    ) -> str:
        """Insert a node. Returns its ID."""
        nid = node_id or _uuid()
        now = _now()
        when = prov_when or now
        self.conn.execute(
            """INSERT OR REPLACE INTO nodes
               (id, type, title, content, aka, intent,
                prov_who, prov_when, prov_activity, prov_why, prov_source,
                weight, domains, status, audience,
                created_at, updated_at, last_accessed, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (nid, node_type, title, content,
             _jdumps(aka or []), intent,
             _jdumps(prov_who or []), when, prov_activity, prov_why, prov_source,
             weight, _jdumps(domains or []), status, audience,
             now, now, now, _jdumps(extra or {})),
        )
        self.conn.commit()
        actor = (prov_who or [""])[0] if prov_who else ""
        self._log("add_node", nid, title, actor,
                  {"type": node_type, "activity": prov_activity})

        # Auto-create person nodes from prov_who entries
        if prov_who and node_type != "person" and prov_activity not in ("auto-created", ""):
            for person_name in prov_who:
                if not person_name:
                    continue
                existing_person = self.get_node_by_title(person_name)
                if existing_person is None:
                    existing_person = self.get_node(person_name)
                if existing_person is None:
                    self.add_node(
                        person_name,
                        node_type="person",
                        prov_activity="auto-created",
                        prov_why=f"Referenced in prov_who of '{title}'",
                    )
                    existing_person = self.get_node_by_title(person_name)
                if existing_person:
                    self.add_edge(nid, existing_person["id"],
                                  edge_type="context_of",
                                  weight=0.4,
                                  provenance="auto-linked from prov_who",
                                  bidirectional=False)

        return nid

    def get_node(self, node_id: str) -> dict | None:
        """Fetch a node by ID, updating last_accessed."""
        row = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if row is None:
            return None
        self.conn.execute(
            "UPDATE nodes SET last_accessed = ? WHERE id = ?", (_now(), node_id))
        self.conn.commit()
        return self._row_to_dict(row)

    def get_node_by_title(self, title: str) -> dict | None:
        """Match by title or AKA (case-insensitive)."""
        # Exact title match
        row = self.conn.execute(
            "SELECT * FROM nodes WHERE lower(title) = lower(?)", (title,)).fetchone()
        if row:
            return self._row_to_dict(row)
        # AKA match: search JSON array for alias
        lower = title.lower()
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE aka != '[]' AND aka != ''").fetchall()
        for r in rows:
            d = self._row_to_dict(r)
            if any(a.lower() == lower for a in (d.get("aka") or [])):
                return d
        return None

    def update_node(self, node_id: str, **fields) -> None:
        """Update specific fields on a node."""
        allowed = {"title", "content", "aka", "intent", "weight", "domains",
                   "status", "audience", "prov_who", "prov_activity", "prov_why", "prov_source", "extra"}
        updates = {}
        for k, v in fields.items():
            if k not in allowed:
                continue
            if isinstance(v, (list, dict)):
                v = _jdumps(v)
            updates[k] = v

        if not updates:
            return

        updates["updated_at"] = _now()
        sets = ", ".join(f"{k} = ?" for k in updates)
        vals = list(updates.values()) + [node_id]
        self.conn.execute(f"UPDATE nodes SET {sets} WHERE id = ?", vals)
        self.conn.commit()
        self._log("update_node", node_id, "",
                  details={"fields": list(fields.keys())})

    def delete_node(self, node_id: str) -> None:
        # Capture title before deletion for logging
        row = self.conn.execute("SELECT title FROM nodes WHERE id = ?", (node_id,)).fetchone()
        title = row["title"] if row else node_id
        self.conn.execute("DELETE FROM edges WHERE from_id = ? OR to_id = ?",
                          (node_id, node_id))
        self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        self.conn.commit()
        self._log("delete_node", node_id, title)

    def all_nodes(self, node_type: str | None = None,
                  status: str | None = None,
                  audience: str | None = None,
                  limit: int = 500) -> list[dict]:
        """List nodes with optional type/status/audience filters."""
        q = "SELECT * FROM nodes WHERE 1=1"
        params: list = []
        if node_type:
            q += " AND type = ?"
            params.append(node_type)
        if status:
            q += " AND status = ?"
            params.append(status)
        if audience:
            q += " AND audience = ?"
            params.append(audience)
        q += " ORDER BY weight DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recent_nodes(self, n: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM nodes ORDER BY updated_at DESC LIMIT ?", (n,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Edge operations ────────────────────────────────────────────────

    def add_edge(self, from_id: str, to_id: str, edge_type: str = "relates_to",
                 weight: float = 0.5, provenance: str = "",
                 bidirectional: bool = True) -> None:
        """Add an edge. Bidirectional by default (enforces graph invariant)."""
        self.conn.execute(
            """INSERT OR REPLACE INTO edges (from_id, to_id, type, weight, provenance)
               VALUES (?, ?, ?, ?, ?)""",
            (from_id, to_id, edge_type, weight, provenance),
        )
        if bidirectional:
            self.conn.execute(
                """INSERT OR IGNORE INTO edges (from_id, to_id, type, weight, provenance)
                   VALUES (?, ?, ?, ?, ?)""",
                (to_id, from_id, edge_type, weight * 0.8, provenance),
            )
        self.conn.commit()
        self._log("add_edge", f"{from_id}->{to_id}", "",
                  details={"type": edge_type, "weight": weight})

    def edges_from(self, node_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT e.*, n.title as to_title FROM edges e
               JOIN nodes n ON n.id = e.to_id
               WHERE e.from_id = ? ORDER BY e.weight DESC""",
            (node_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def edges_to(self, node_id: str) -> list[dict]:
        rows = self.conn.execute(
            """SELECT e.*, n.title as from_title FROM edges e
               JOIN nodes n ON n.id = e.from_id
               WHERE e.to_id = ? ORDER BY e.weight DESC""",
            (node_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def orphans(self) -> list[dict]:
        """Nodes with no edges (violates graph health invariant)."""
        rows = self.conn.execute(
            """SELECT * FROM nodes WHERE id NOT IN
               (SELECT from_id FROM edges UNION SELECT to_id FROM edges)"""
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── FTS5 search ────────────────────────────────────────────────────

    def fts_search(self, query: str, limit: int = 20) -> list[dict]:
        """Full-text search using FTS5 BM25 ranking."""
        # Escape special FTS5 characters
        safe_query = query.replace('"', '""')
        try:
            rows = self.conn.execute(
                """SELECT n.*, rank FROM nodes_fts
                   JOIN nodes n ON n.id = nodes_fts.id
                   WHERE nodes_fts MATCH ?
                   ORDER BY rank LIMIT ?""",
                (f'"{safe_query}" OR {safe_query}', limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback: simple LIKE search if FTS query syntax fails
            rows = self.conn.execute(
                """SELECT *, 0 as rank FROM nodes
                   WHERE title LIKE ? OR content LIKE ?
                   ORDER BY weight DESC LIMIT ?""",
                (f"%{query}%", f"%{query}%", limit),
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Weight decay ───────────────────────────────────────────────────

    def apply_weight_decay(self, node_half_life_days: int = 90,
                           edge_half_life_days: int = 30) -> int:
        """Decay weights based on last access time. Returns count of affected nodes."""
        now = datetime.now()

        # Node decay
        rows = self.conn.execute("SELECT id, weight, last_accessed FROM nodes").fetchall()
        count = 0
        for row in rows:
            try:
                last = datetime.fromisoformat(row["last_accessed"])
            except (ValueError, TypeError):
                continue
            days_since = (now - last).days
            if days_since <= 0:
                continue
            decay = 0.5 ** (days_since / node_half_life_days)
            new_weight = max(0.01, row["weight"] * decay)
            if abs(new_weight - row["weight"]) > 0.001:
                self.conn.execute(
                    "UPDATE nodes SET weight = ? WHERE id = ?",
                    (round(new_weight, 4), row["id"]),
                )
                count += 1

        # Edge decay
        edge_rows = self.conn.execute("SELECT id, weight, created_at FROM edges").fetchall()
        for row in edge_rows:
            try:
                created = datetime.fromisoformat(row["created_at"])
            except (ValueError, TypeError):
                continue
            days_since = (now - created).days
            if days_since <= 0:
                continue
            decay = 0.5 ** (days_since / edge_half_life_days)
            new_weight = max(0.01, row["weight"] * decay)
            if abs(new_weight - row["weight"]) > 0.001:
                self.conn.execute(
                    "UPDATE edges SET weight = ? WHERE id = ?",
                    (round(new_weight, 4), row["id"]),
                )

        self.conn.commit()
        return count

    # ── Operational node queries ────────────────────────────────────────

    def nodes_by_trigger(self, trigger: str, node_type: str | None = None) -> list[dict]:
        """Find operational nodes (constraints, checkpoints) matching a trigger.

        Trigger is stored in extra JSON: {"trigger": "pre-deploy"}.
        """
        q = "SELECT * FROM nodes WHERE extra LIKE ?"
        params: list = [f'%"trigger"%{trigger}%']
        if node_type:
            q += " AND type = ?"
            params.append(node_type)
        q += " AND status = 'active' ORDER BY weight DESC"
        rows = self.conn.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def nodes_by_owner(self, owner: str, node_type: str | None = None) -> list[dict]:
        """Find nodes owned by a specific person (watches, directives)."""
        q = "SELECT * FROM nodes WHERE extra LIKE ?"
        params: list = [f'%"owner"%"{owner}"%']
        if node_type:
            q += " AND type = ?"
            params.append(node_type)
        q += " AND status = 'active' ORDER BY weight DESC"
        rows = self.conn.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def active_watches(self) -> list[dict]:
        """Get all active watches that haven't expired."""
        now = _now()[:10]  # YYYY-MM-DD
        rows = self.conn.execute(
            """SELECT * FROM nodes WHERE type = 'watch' AND status = 'active'
               ORDER BY weight DESC"""
        ).fetchall()
        result = []
        for r in rows:
            d = self._row_to_dict(r)
            expires = (d.get("extra") or {}).get("expires", "")
            # Include if no expiry or not yet expired
            if not expires or expires >= now:
                result.append(d)
        return result

    def active_constraints(self, trigger: str | None = None) -> list[dict]:
        """Get active constraints, optionally filtered by trigger."""
        if trigger:
            return self.nodes_by_trigger(trigger, node_type="constraint")
        return self.all_nodes(node_type="constraint", status="active")

    def active_checkpoints(self, trigger: str | None = None) -> list[dict]:
        """Get active checkpoints, optionally filtered by trigger."""
        if trigger:
            return self.nodes_by_trigger(trigger, node_type="checkpoint")
        return self.all_nodes(node_type="checkpoint", status="active")

    def operational_summary(self, trigger: str | None = None,
                            owner: str | None = None) -> dict:
        """Summary of all active operational nodes."""
        constraints = self.active_constraints(trigger)
        checkpoints = self.active_checkpoints(trigger)
        watches = self.active_watches()
        directives = self.all_nodes(node_type="directive", status="active")

        if owner:
            watches = [w for w in watches if (w.get("extra") or {}).get("owner") == owner]
            directives = [d for d in directives if (d.get("extra") or {}).get("owner") == owner]

        return {
            "constraints": constraints,
            "checkpoints": checkpoints,
            "watches": watches,
            "directives": directives,
        }

    # ── Meta key-value ────────────────────────────────────────────────

    def get_meta(self, key: str) -> str | None:
        """Read a value from the meta table. Returns None if not found."""
        row = self.conn.execute(
            "SELECT value FROM meta WHERE key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        return row["value"]

    def set_meta(self, key: str, value: str) -> None:
        """Write a value to the meta table (upsert)."""
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
            (key, value),
        )
        self.conn.commit()

    # ── Skill tracking ─────────────────────────────────────────────────

    def record_skill_evidence(self, person_id: str, skill_title: str,
                              evidence: str, source: str = "") -> None:
        """Record evidence of a person demonstrating a skill.

        - Find or create the person node
        - Find or create the skill node
        - Add/update a 'demonstrates' edge from person to skill
        - Store evidence in edge provenance
        - Boost the skill node weight by 0.05 per evidence (cap at 1.0)
        """
        # Find or create person node
        person_node = self.get_node(person_id)
        if person_node is None:
            person_node = self.get_node_by_title(person_id)
        if person_node is None:
            person_id = self.add_node(
                person_id,
                node_type="person",
                prov_activity="skill-tracking",
                prov_why="Auto-created for skill evidence",
            )
        else:
            person_id = person_node["id"]

        # Find or create skill node
        skill_node = self.get_node_by_title(skill_title)
        if skill_node is None:
            skill_id = self.add_node(
                skill_title,
                node_type="skill",
                weight=0.5,
                prov_activity="skill-tracking",
                prov_why="Auto-created for skill evidence",
            )
        else:
            skill_id = skill_node["id"]

        # Build evidence record
        now = _now()

        # Check for existing demonstrates edge
        existing = self.conn.execute(
            """SELECT id, provenance FROM edges
               WHERE from_id = ? AND to_id = ? AND type = 'demonstrates'""",
            (person_id, skill_id),
        ).fetchone()

        if existing:
            # Append evidence to existing provenance
            try:
                prev = json.loads(existing["provenance"])
                if isinstance(prev, list):
                    prev.append({"evidence": evidence, "source": source, "recorded_at": now})
                else:
                    prev = [prev, {"evidence": evidence, "source": source, "recorded_at": now}]
            except (json.JSONDecodeError, TypeError):
                prev = [{"evidence": evidence, "source": source, "recorded_at": now}]
            self.conn.execute(
                "UPDATE edges SET provenance = ? WHERE id = ?",
                (_jdumps(prev), existing["id"]),
            )
            self.conn.commit()
        else:
            # Create new demonstrates edge (unidirectional — person -> skill)
            prov_list = [{"evidence": evidence, "source": source, "recorded_at": now}]
            self.conn.execute(
                """INSERT OR REPLACE INTO edges (from_id, to_id, type, weight, provenance)
                   VALUES (?, ?, 'demonstrates', 0.5, ?)""",
                (person_id, skill_id, _jdumps(prov_list)),
            )
            self.conn.commit()

        # Boost skill weight by 0.05, capped at 1.0
        skill_node = self.get_node(skill_id)
        if skill_node:
            new_weight = min(1.0, skill_node["weight"] + 0.05)
            self.update_node(skill_id, weight=new_weight)

        self._log("record_skill_evidence", skill_id, skill_title, person_id,
                  {"evidence": evidence, "source": source})

    # ── Directive mutable state ─────────────────────────────────────────

    def update_directive_state(self, node_id: str, state: dict) -> None:
        """Update the current_state of a directive/operational node."""
        node = self.get_node(node_id)
        if not node:
            return
        extra = node.get("extra") or {}
        extra["current_state"] = state
        extra["state_updated_at"] = _now()
        self.update_node(node_id, extra=extra)
        self._log("update_state", node_id, node.get("title", ""),
                  details={"state": state})

    # ── Stats ──────────────────────────────────────────────────────────

    def stats(self) -> dict:
        node_count = self.conn.execute("SELECT COUNT(*) FROM nodes").fetchone()[0]
        edge_count = self.conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        orphan_count = len(self.orphans())
        type_counts = {}
        for row in self.conn.execute("SELECT type, COUNT(*) as c FROM nodes GROUP BY type"):
            type_counts[row["type"]] = row["c"]
        return {
            "nodes": node_count,
            "edges": edge_count,
            "orphans": orphan_count,
            "types": type_counts,
        }

    # ── Helpers ────────────────────────────────────────────────────────

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        for key in ("aka", "domains", "prov_who", "extra"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def node_ids(self) -> list[str]:
        """All node IDs."""
        return [r[0] for r in self.conn.execute("SELECT id FROM nodes").fetchall()]
