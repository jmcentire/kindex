"""SQLite store — primary persistence layer for Kindex."""

from __future__ import annotations

import datetime as _dt
import json
import re
import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable


def _json_default(obj):
    if isinstance(obj, (_dt.date, _dt.datetime)):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    raise TypeError(f"Not JSON serializable: {type(obj)}")


def _jdumps(obj):
    return json.dumps(obj, default=_json_default)

from .config import Config
from .schema import CREATE_TABLES, SCHEMA_VERSION, edit_class_for


def _now() -> str:
    return datetime.now(tz=None).isoformat(timespec="seconds")


def _uuid() -> str:
    return uuid.uuid4().hex[:12]


class EditPolicyError(ValueError):
    """An edit was refused by the node-type edit policy."""


class LockHeldError(RuntimeError):
    """The node is locked by another agent and force was not given."""


class ProfileMismatchError(RuntimeError):
    """The database is stamped for a different profile than the active one."""


# Extra-JSON keys owned by dedicated subsystems (tasks, sessions,
# coordination, locks). edit_node must never alter these.
RESERVED_EXTRA_KEYS = frozenset({
    "claim", "lock", "coord_status", "session_status", "task_status",
    "current_state", "messages", "members", "resources", "inject_messages",
})

_EXPIRES_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DIFF_TRUNCATE = 500

# Lock-error remedy for the supersede path: neither `kin supersede` nor the
# MCP supersede tool exposes a force flag (per contract), so the message must
# name the remedy that actually exists on those surfaces.
_SUPERSEDE_LOCK_REMEDY = (
    "release the lock first (kin unlock --force / lock_release force=True); "
    "supersede does not take a force flag"
)


def _validate_expires(expires: str) -> None:
    """Accept YYYY-MM-DD only (zero-padded, real calendar date)."""
    if not isinstance(expires, str) or not _EXPIRES_RE.match(expires):
        raise ValueError(f"expires must be YYYY-MM-DD, got {expires!r}")
    try:
        datetime.strptime(expires, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"expires must be a real YYYY-MM-DD date, got {expires!r}"
        ) from None


def _trunc(value: Any, limit: int = _DIFF_TRUNCATE) -> str | None:
    """Stringify a diff value and truncate it for activity-log storage."""
    if value is None:
        return None
    s = value if isinstance(value, str) else _jdumps(value)
    return s if len(s) <= limit else s[:limit]


def active_lock(node: dict) -> dict | None:
    """Return extra['lock'] if present and unexpired, else None.

    Expiry is lazy: an expired lock is treated as absent (callers may
    overwrite it); the daemon sweep clears them from storage eventually.
    """
    extra = node.get("extra") or {}
    lock = extra.get("lock") if isinstance(extra, dict) else None
    if not isinstance(lock, dict):
        return None
    expires_at = lock.get("expires_at")
    if expires_at:
        try:
            if datetime.fromisoformat(expires_at) <= datetime.now():
                return None
        except (ValueError, TypeError):
            pass  # unparseable expiry — keep the lock active rather than dropping it
    return lock


def node_expired(node: dict, today: str | None = None) -> bool:
    """True if extra['expires'] (YYYY-MM-DD) is strictly in the past.

    Matches active_watches() semantics: a node expiring today is still live.
    Generic — usable by hooks/attention/daemon for any node type.
    """
    extra = node.get("extra") or {}
    expires = extra.get("expires") if isinstance(extra, dict) else None
    if not expires or not isinstance(expires, str):
        return False
    today = today or _now()[:10]
    return expires < today


class Store:
    """SQLite-backed knowledge graph with FTS5 full-text search.

    This is the primary query engine. Markdown files remain as
    human-readable canonical source; the store indexes them.
    """

    def __init__(self, config: Config, *, sqlite_timeout: float = 5.0):
        self.config = config
                # Support both kindex.db (new) and conv.db (legacy)
        new_db = config.data_path / "kindex.db"
        old_db = config.data_path / "conv.db"
        self.db_path = old_db if old_db.exists() and not new_db.exists() else new_db
        self._conn: sqlite3.Connection | None = None
        self._sqlite_timeout = max(0.0, float(sqlite_timeout))
        # Profile stamp guard: configs that carry an active_profile (added by
        # the profiles feature) bind this database to that profile name.
        self._expected_profile: str | None = getattr(config, "active_profile", None)

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self.config.data_path.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(
                str(self.db_path),
                timeout=self._sqlite_timeout,
            )
            self._conn.row_factory = sqlite3.Row
            self._conn.execute(f"PRAGMA busy_timeout={int(self._sqlite_timeout * 1000)}")
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._init_schema()
            self._check_profile_stamp()
        return self._conn

    def _check_profile_stamp(self) -> None:
        """Enforce the per-database profile stamp (meta key 'kin_profile').

        No active profile -> no stamping, no check (legacy single-graph).
        Active profile + unstamped db -> stamp it, unless the config marks
        this open as a --data-dir override (_stamp_on_open False): an
        explicit override must never bind a foreign database to the active
        profile. An existing mismatched stamp still hard-refuses.
        Active profile != stamp -> close the connection and raise.
        """
        expected = self._expected_profile
        if not expected:
            return
        row = self._conn.execute(
            "SELECT value FROM meta WHERE key = 'kin_profile'"
        ).fetchone()
        if row is None:
            if not getattr(self.config, "_stamp_on_open", True):
                return
            self._conn.execute(
                "INSERT OR REPLACE INTO meta (key, value) VALUES ('kin_profile', ?)",
                (expected,),
            )
            self._conn.commit()
            return
        stamped = row["value"]
        if stamped != expected:
            conn, self._conn = self._conn, None
            conn.close()
            raise ProfileMismatchError(
                f"Database {self.db_path} is stamped for profile '{stamped}' "
                f"but the active profile is '{expected}'"
            )

    def _init_schema(self) -> None:
        # Check if this is an existing database that needs migration
        # before applying the full schema (which includes triggers
        # referencing columns that may not exist yet).
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='meta'"
        )
        has_meta = cur.fetchone() is not None

        if has_meta:
            cur = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
            row = cur.fetchone()
            if row is not None:
                current = int(row["value"])
                if current < SCHEMA_VERSION:
                    self._migrate_schema(current)
        elif self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='nodes'"
        ).fetchone() is not None:
            # Pre-versioning database: has nodes table but no meta table.
            # Create meta table, then migrate from v1.
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);"
            )
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', '1')"
            )
            self._conn.commit()
            self._migrate_schema(1)

        # Now safe to apply full schema (IF NOT EXISTS is idempotent
        # once columns are up to date).
        self._conn.executescript(CREATE_TABLES)

        # Ensure schema version is set for fresh databases.
        cur = self._conn.execute("SELECT value FROM meta WHERE key='schema_version'")
        if cur.fetchone() is None:
            self._conn.execute(
                "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
                (str(SCHEMA_VERSION),),
            )
            self._conn.commit()

    def _migrate_schema(self, current_version: int) -> None:
        """Apply incremental schema migrations. Uses self._conn directly
        to avoid triggering the conn property (which calls _init_schema)."""
        c = self._conn
        if current_version < 2:
            # v2: add audience column
            try:
                c.execute("ALTER TABLE nodes ADD COLUMN audience TEXT NOT NULL DEFAULT 'private'")
                c.execute("CREATE INDEX IF NOT EXISTS idx_nodes_audience ON nodes(audience)")
                c.commit()
            except Exception:
                pass  # column already exists

        if current_version < 3:
            # v3: add activity_log table
            try:
                c.executescript("""
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
                c.commit()
            except Exception:
                pass

        if current_version < 4:
            # v4: add suggestions table
            try:
                c.executescript("""
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
                    CREATE INDEX IF NOT EXISTS idx_suggestions_status_created
                        ON suggestions(status, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_suggestions_status_pair
                        ON suggestions(status, concept_a, concept_b);
                """)
                c.commit()
            except Exception:
                pass

        if current_version < 5:
            # v5: add reminders table
            try:
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS reminders (
                        id TEXT PRIMARY KEY,
                        title TEXT NOT NULL,
                        body TEXT DEFAULT '',
                        priority TEXT DEFAULT 'normal',
                        status TEXT DEFAULT 'active',
                        reminder_type TEXT DEFAULT 'once',
                        schedule TEXT DEFAULT '',
                        next_due TEXT NOT NULL,
                        last_fired TEXT,
                        snooze_until TEXT,
                        snooze_count INTEGER DEFAULT 0,
                        channels TEXT DEFAULT '[]',
                        related_node_id TEXT,
                        tags TEXT DEFAULT '',
                        extra TEXT DEFAULT '{}',
                        created_at TEXT DEFAULT (datetime('now')),
                        updated_at TEXT DEFAULT (datetime('now'))
                    );
                    CREATE INDEX IF NOT EXISTS idx_reminders_status ON reminders(status);
                    CREATE INDEX IF NOT EXISTS idx_reminders_next_due ON reminders(next_due);
                    CREATE INDEX IF NOT EXISTS idx_reminders_priority ON reminders(priority);
                """)
                c.commit()
            except Exception:
                pass

        if current_version < 6:
            # v6: index suggestions for scheduled dream workloads
            try:
                c.executescript("""
                    CREATE INDEX IF NOT EXISTS idx_suggestions_status_created
                        ON suggestions(status, created_at DESC);
                    CREATE INDEX IF NOT EXISTS idx_suggestions_status_pair
                        ON suggestions(status, concept_a, concept_b);
                """)
                c.commit()
            except Exception:
                pass

        if current_version < 7:
            # v7: stigmergic injection pheromone (retrieval channel, not topology)
            try:
                c.executescript("""
                    CREATE TABLE IF NOT EXISTS injection_pheromone (
                        node_id TEXT NOT NULL REFERENCES nodes(id),
                        context TEXT NOT NULL DEFAULT '',
                        strength REAL NOT NULL DEFAULT 0.0,
                        deposits INTEGER NOT NULL DEFAULT 0,
                        reinforcements INTEGER NOT NULL DEFAULT 0,
                        missed INTEGER NOT NULL DEFAULT 0,
                        last_deposit TEXT NOT NULL DEFAULT (datetime('now')),
                        last_decay TEXT NOT NULL DEFAULT (datetime('now')),
                        PRIMARY KEY (node_id, context)
                    );
                    CREATE INDEX IF NOT EXISTS idx_pheromone_node
                        ON injection_pheromone(node_id);
                    CREATE INDEX IF NOT EXISTS idx_pheromone_strength
                        ON injection_pheromone(strength DESC);
                """)
                c.commit()
            except Exception:
                pass

        if current_version < SCHEMA_VERSION:
            c.execute(
                "UPDATE meta SET value = ? WHERE key = 'schema_version'",
                (str(SCHEMA_VERSION),),
            )
            c.commit()

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

    def suggestion_exists(
        self,
        concept_a: str,
        concept_b: str,
        *,
        status: str = "pending",
    ) -> bool:
        """Return true if a suggestion exists for this pair in either order."""
        row = self.conn.execute(
            """
            SELECT 1 FROM suggestions
             WHERE status = ? AND concept_a = ? AND concept_b = ?
            UNION ALL
            SELECT 1 FROM suggestions
             WHERE status = ? AND concept_a = ? AND concept_b = ?
            LIMIT 1
            """,
            (status, concept_a, concept_b, status, concept_b, concept_a),
        ).fetchone()
        return row is not None

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
        tags: list[str] | None = None,
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
        # Merge user-supplied tags into domains (supplement, never replace)
        if tags:
            domains = list(set((domains or []) + tags))
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

        # Auto-embed for vector search (best-effort, no failure propagation)
        try:
            from .vectors import is_available, upsert_embedding
            if is_available():
                embed_text = f"{title} {content}".strip()
                if embed_text:
                    upsert_embedding(self, nid, embed_text)
        except Exception:
            pass  # vectors not installed or embed failed — node still created

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

    def update_node(self, node_id: str, _log_activity: bool = True,
                    **fields) -> None:
        """Update specific fields on a node.

        ``_log_activity`` is private: internal callers that write their own
        richer activity entry (edit_node) pass False to avoid double-logging
        the same change. External semantics are unchanged.
        """
        allowed = {"title", "content", "aka", "intent", "weight", "domains",
                   "tags", "status", "audience", "prov_who", "prov_activity",
                   "prov_why", "prov_source", "extra"}
        # Handle tags -> domains alias (tags supplement, never replace)
        if "tags" in fields:
            tag_vals = fields.pop("tags")
            if isinstance(tag_vals, list):
                existing = fields.get("domains") or []
                fields["domains"] = list(set(existing + tag_vals))
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
        if _log_activity:
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
        # Drop the vector embedding too (best-effort — table may not exist)
        try:
            from .vectors import delete_embedding
            delete_embedding(self, node_id)
        except Exception:
            pass
        self._log("delete_node", node_id, title)

    def all_nodes(self, node_type: str | None = None,
                  status: str | None = None,
                  audience: str | None = None,
                  tags: list[str] | None = None,
                  limit: int = 500) -> list[dict]:
        """List nodes with optional type/status/audience/tags filters."""
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
        if tags:
            for tag in tags:
                q += " AND domains LIKE ?"
                params.append(f'%"{tag}"%')
        q += " ORDER BY weight DESC, updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(q, params).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def recent_nodes(self, n: int = 20) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM nodes ORDER BY updated_at DESC LIMIT ?", (n,)
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    def nodes_with_expiry(self, status: str = "active",
                          limit: int = 1000) -> list[dict]:
        """Nodes carrying extra['expires'] (cheap LIKE prefilter).

        The `"expires"` pattern (quote-delimited) deliberately does not match
        `"expires_at"` lock timestamps. Callers confirm with node_expired().
        """
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE status = ? AND extra LIKE ? "
            "ORDER BY updated_at DESC LIMIT ?",
            (status, '%"expires"%', limit),
        ).fetchall()
        return [self._row_to_dict(r) for r in rows]

    # ── Edit / supersede / atomic extra ─────────────────────────────────

    def _check_lock(self, node: dict, actor: str | None, force: bool,
                    *, remedy: str = "pass force to override") -> None:
        """Raise LockHeldError on a foreign unexpired lock unless force.

        `remedy` is the actionable hint appended to the error — callers
        whose surface has no force flag (e.g. supersede) pass one that
        names a remedy that actually exists on that surface.
        """
        if force:
            return
        lock = active_lock(node)
        if lock is None:
            return
        holder = lock.get("agent") or ""
        if actor is not None and holder == actor:
            return
        until = f" until {lock['expires_at']}" if lock.get("expires_at") else ""
        raise LockHeldError(
            f"Node {node.get('id')} is locked by '{holder or 'unknown'}'{until} "
            f"— {remedy}"
        )

    @staticmethod
    def _check_mutable_status(node: dict, force: bool) -> None:
        """Refuse edit/supersede on dead nodes.

        - superseded: always refused (force does NOT bypass) — the error
          names the successor so the caller can retarget it.
        - archived: refused unless force.
        """
        status = node.get("status") or "active"
        if status == "superseded":
            successor = (node.get("extra") or {}).get("superseded_by") or "unknown"
            raise EditPolicyError(
                f"Node {node.get('id')} was superseded by {successor} "
                f"— operate on that node instead"
            )
        if status == "archived" and not force:
            raise EditPolicyError(
                f"Node {node.get('id')} is archived — pass force to modify it"
            )

    def edit_node(
        self,
        node_id: str,
        actor: str | None = None,
        force: bool = False,
        policy_overrides: dict[str, str] | None = None,
        *,
        title: str | None = None,
        content: str | None = None,
        append: str | None = None,
        add_tags: list[str] | None = None,
        remove_tags: list[str] | None = None,
        intent: str | None = None,
        aka: list[str] | None = None,
        expires: str | None = None,
    ) -> dict:
        """Policy-aware node edit. Routes through update_node (UPDATE only).

        - editable types: all fields allowed
        - additive types: only append + expires (replacement -> supersede_node)
        - managed types: always refused (task/session/coordination tooling owns them)
        Refuses a foreign unexpired lock unless force. Logs per-field old/new
        diffs to the activity log. Reserved extra keys are never altered.
        Returns the updated node dict.
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"No node with id '{node_id}'")

        provided = {
            name: val for name, val in (
                ("title", title), ("content", content), ("append", append),
                ("add_tags", add_tags), ("remove_tags", remove_tags),
                ("intent", intent), ("aka", aka), ("expires", expires),
            ) if val is not None
        }
        if not provided:
            raise ValueError("edit_node requires at least one field to change")

        node_type = node.get("type", "concept")
        cls = edit_class_for(node_type, policy_overrides)
        if cls == "managed":
            raise EditPolicyError(
                f"Node type '{node_type}' is managed — edit is not allowed; "
                f"use the dedicated task/session/coordination tools"
            )
        if cls == "additive":
            disallowed = sorted(set(provided) - {"append", "expires"})
            if disallowed:
                raise EditPolicyError(
                    f"Node type '{node_type}' is additive — only append and "
                    f"expires are allowed (got: {', '.join(disallowed)}); "
                    f"use supersede to replace it"
                )

        self._check_mutable_status(node, force)
        self._check_lock(node, actor, force)
        if expires is not None:
            _validate_expires(expires)

        updates: dict[str, Any] = {}
        diffs: dict[str, dict] = {}

        old_title = node.get("title") or ""
        new_title = old_title
        if title is not None and title != old_title:
            updates["title"] = title
            diffs["title"] = {"old": _trunc(old_title), "new": _trunc(title)}
            new_title = title

        old_content = node.get("content") or ""
        new_content = content if content is not None else old_content
        if append:
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            header = f"[addendum {stamp} {actor}]" if actor else f"[addendum {stamp}]"
            prefix = f"{new_content}\n\n" if new_content else ""
            new_content = f"{prefix}{header}\n{append}"
        if new_content != old_content:
            updates["content"] = new_content
            diffs["content"] = {"old": _trunc(old_content), "new": _trunc(new_content)}

        if intent is not None and intent != (node.get("intent") or ""):
            updates["intent"] = intent
            diffs["intent"] = {"old": _trunc(node.get("intent") or ""),
                               "new": _trunc(intent)}

        if aka is not None:
            old_aka = list(node.get("aka") or [])
            if list(aka) != old_aka:
                updates["aka"] = list(aka)
                diffs["aka"] = {"old": _trunc(old_aka), "new": _trunc(list(aka))}

        if "title" in updates and old_title:
            # A rename keeps the old title reachable: preserve it as an alias
            # so title-keyed dedup (session capture, inbox ingest, dream_deep
            # idempotency) still matches via get_node_by_title's AKA path.
            base_aka = list(updates.get("aka", node.get("aka") or []))
            lowered = {a.lower() for a in base_aka}
            if (old_title.lower() != new_title.lower()
                    and old_title.lower() not in lowered):
                preserved = base_aka + [old_title]
                updates["aka"] = preserved
                diffs["aka"] = {"old": _trunc(list(node.get("aka") or [])),
                                "new": _trunc(preserved)}

        if add_tags or remove_tags:
            old_domains = list(node.get("domains") or [])
            removed = set(remove_tags or [])
            new_domains = [d for d in old_domains if d not in removed]
            for t in add_tags or []:
                if t not in new_domains:
                    new_domains.append(t)
            if new_domains != old_domains:
                updates["domains"] = new_domains
                diffs["tags"] = {"old": _trunc(old_domains), "new": _trunc(new_domains)}

        exp_diff: dict[str, Any] = {}
        if expires is not None:
            # The expires merge must not write the extra column from the
            # pre-check snapshot: a lock (or any extra key) committed by a
            # concurrent agent between get_node above and the write would be
            # silently erased. Route it through atomic_extra_update and
            # re-check the lock against the fresh in-transaction state —
            # LockHeldError raised here propagates and rolls back.
            def _set_expires(fresh: dict) -> None:
                self._check_lock({"id": node_id, "extra": fresh}, actor, force)
                old_expires = fresh.get("expires")
                if expires != old_expires:
                    fresh["expires"] = expires
                    exp_diff["old"] = old_expires
                    exp_diff["new"] = expires

            self.atomic_extra_update(node_id, _set_expires)
            if exp_diff:
                diffs["expires"] = {"old": exp_diff["old"],
                                    "new": exp_diff["new"]}

        if updates:
            # _log_activity=False: edit_node writes its own (richer) entry
            # below — without it every edit appears twice in the changelog.
            self.update_node(node_id, _log_activity=False, **updates)
        if updates or exp_diff:
            self._log("edit_node", node_id, updates.get("title", old_title),
                      actor or "", {"diffs": diffs, "type": node_type})
            if "title" in updates or "content" in updates:
                # Re-embed for vector search (best-effort, like add_node)
                try:
                    from .vectors import is_available, upsert_embedding
                    if is_available():
                        embed_text = f"{new_title} {new_content}".strip()
                        if embed_text:
                            upsert_embedding(self, node_id, embed_text)
                except Exception:
                    pass  # vectors not installed or embed failed — edit persisted

        return self.get_node(node_id)

    def supersede_node(
        self,
        node_id: str,
        new_text: str,
        actor: str | None = None,
        expires: str | None = None,
        reason: str | None = None,
        force: bool = False,
        policy_overrides: dict[str, str] | None = None,
    ) -> dict:
        """Replace a node with a fresh one, preserving history.

        Single transaction: insert the new node (same type/tags/audience/
        intent, fresh id), edge new-[supersedes]->old, mark the old node
        status='superseded' with extra['superseded_by'], and migrate its
        injection_pheromone trails to the new node. Lock check as edit_node.
        Managed types (task/session/coordination) are refused like edit_node
        — their tooling owns their state. Superseded nodes cannot be
        superseded again (the error names the successor); archived nodes
        require force. Returns the new node dict.
        """
        node = self.get_node(node_id)
        if node is None:
            raise KeyError(f"No node with id '{node_id}'")
        node_type = node.get("type", "concept")
        cls = edit_class_for(node_type, policy_overrides)
        if cls == "managed":
            raise EditPolicyError(
                f"Node type '{node_type}' is managed — supersede is not "
                f"allowed; use the dedicated task/session/coordination tools"
            )
        self._check_mutable_status(node, force)
        self._check_lock(node, actor, force, remedy=_SUPERSEDE_LOCK_REMEDY)
        text = (new_text or "").strip()
        if not text:
            raise ValueError("supersede_node requires non-empty new_text")
        if expires is not None:
            _validate_expires(expires)

        new_id = _uuid()
        now = _now()
        title = text[:60].strip()  # same title convention as MCP add
        new_extra: dict[str, Any] = {"supersedes": node_id}
        if expires:
            new_extra["expires"] = expires
        if reason:
            new_extra["supersede_reason"] = reason

        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            # Re-verify status inside the transaction: two concurrent
            # supersedes serialize on BEGIN IMMEDIATE, so the second one
            # must see the first's status flip and abort instead of
            # creating a competing successor.
            row = conn.execute(
                "SELECT status, extra FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No node with id '{node_id}'")
            try:
                cur_extra = json.loads(row["extra"] or "{}")
            except (json.JSONDecodeError, TypeError):
                cur_extra = {}
            if not isinstance(cur_extra, dict):
                cur_extra = {}
            cur_status = row["status"] or "active"
            if cur_status == "superseded":
                successor = cur_extra.get("superseded_by") or "unknown"
                raise EditPolicyError(
                    f"Node {node_id} was superseded by {successor} "
                    f"— operate on that node instead"
                )
            if cur_status == "archived" and not force:
                raise EditPolicyError(
                    f"Node {node_id} is archived — pass force to modify it"
                )
            # Re-check the lock against the fresh in-transaction extra: a
            # lock acquired between the pre-flight snapshot and BEGIN
            # IMMEDIATE must still block the supersede (LockHeldError
            # propagates to the rollback handler below).
            self._check_lock({"id": node_id, "extra": cur_extra}, actor, force,
                             remedy=_SUPERSEDE_LOCK_REMEDY)
            old_extra = dict(cur_extra)
            old_extra["superseded_by"] = new_id
            conn.execute(
                """INSERT INTO nodes
                   (id, type, title, content, aka, intent,
                    prov_who, prov_when, prov_activity, prov_why, prov_source,
                    weight, domains, status, audience,
                    created_at, updated_at, last_accessed, extra)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id, node.get("type", "concept"), title, text,
                 _jdumps([]), node.get("intent") or "",
                 _jdumps([actor] if actor else []), now, "supersede",
                 reason or f"Supersedes '{node.get('title', '')}'", node_id,
                 node.get("weight", 0.5), _jdumps(node.get("domains") or []),
                 "active", node.get("audience", "private"),
                 now, now, now, _jdumps(new_extra)),
            )
            conn.execute(
                "INSERT OR REPLACE INTO edges (from_id, to_id, type, weight, provenance) "
                "VALUES (?, ?, 'supersedes', 0.9, ?)",
                (new_id, node_id,
                 f"superseded by {actor}" if actor else "superseded"),
            )
            conn.execute(
                "UPDATE nodes SET status = 'superseded', extra = ?, updated_at = ? "
                "WHERE id = ?",
                (_jdumps(old_extra), now, node_id),
            )
            conn.execute(
                "UPDATE OR REPLACE injection_pheromone SET node_id = ? "
                "WHERE node_id = ?",
                (new_id, node_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise

        self._log("supersede_node", new_id, title, actor or "",
                  {"superseded": node_id, "reason": reason or ""})

        # Drop the old node's embedding so vector search stops surfacing the
        # superseded text (best-effort — table may not exist).
        try:
            from .vectors import delete_embedding
            delete_embedding(self, node_id)
        except Exception:
            pass

        # Embed the replacement for vector search (best-effort, like add_node)
        try:
            from .vectors import is_available, upsert_embedding
            if is_available():
                embed_text = f"{title} {text}".strip()
                if embed_text:
                    upsert_embedding(self, new_id, embed_text)
        except Exception:
            pass  # vectors not installed or embed failed — node still created

        return self.get_node(new_id)

    def atomic_extra_update(
        self, node_id: str, mutator: Callable[[dict], dict | None]
    ) -> dict:
        """Atomically read-modify-write a node's extra JSON.

        BEGIN IMMEDIATE serializes concurrent writers (no lost updates across
        Store handles). The mutator may return a replacement dict or mutate
        its argument in place and return None. Returns the final extra dict.
        Raises KeyError on a missing node.
        """
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT extra FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"No node with id '{node_id}'")
            try:
                extra = json.loads(row["extra"] or "{}")
            except (json.JSONDecodeError, TypeError):
                extra = {}
            if not isinstance(extra, dict):
                extra = {}
            replacement = mutator(extra)
            final = extra if replacement is None else replacement
            conn.execute(
                "UPDATE nodes SET extra = ?, updated_at = ? WHERE id = ?",
                (_jdumps(final), _now(), node_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return final

    def atomic_archive_expired(self, node_id: str, expired_at: str) -> bool:
        """Archive a node iff its fresh extra['expires'] is still past.

        BEGIN IMMEDIATE re-reads the row, so an expiry extended (or removed)
        after the caller's snapshot wins: the node is left untouched and
        False is returned. Only active nodes are archived; the
        extra['expired_at'] stamp and the status flip land in the same
        UPDATE. Returns True when the node was archived.
        """
        conn = self.conn
        conn.execute("BEGIN IMMEDIATE")
        try:
            row = conn.execute(
                "SELECT status, extra FROM nodes WHERE id = ?", (node_id,)
            ).fetchone()
            fresh_extra: dict | None = None
            if row is not None and (row["status"] or "active") == "active":
                try:
                    parsed = json.loads(row["extra"] or "{}")
                except (json.JSONDecodeError, TypeError):
                    parsed = {}
                if isinstance(parsed, dict) and node_expired({"extra": parsed}):
                    fresh_extra = parsed
            if fresh_extra is None:
                conn.rollback()
                return False
            fresh_extra["expired_at"] = expired_at
            conn.execute(
                "UPDATE nodes SET status = 'archived', extra = ?, "
                "updated_at = ? WHERE id = ?",
                (_jdumps(fresh_extra), _now(), node_id),
            )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        return True

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
        import re
        # Strip punctuation and FTS5 special chars, keep only words
        tokens = re.findall(r'\w+', query.lower())
        if not tokens:
            return []
        # Build FTS5 query: quoted phrase OR individual tokens
        phrase = " ".join(tokens)
        safe_phrase = phrase.replace('"', '""')
        token_expr = " OR ".join(tokens)
        fts_query = f'"{safe_phrase}" OR {token_expr}'
        try:
            rows = self.conn.execute(
                """SELECT n.*, rank FROM nodes_fts
                   JOIN nodes n ON n.id = nodes_fts.id
                   WHERE nodes_fts MATCH ? AND n.status != 'superseded'
                   ORDER BY rank LIMIT ?""",
                (fts_query, limit),
            ).fetchall()
        except sqlite3.OperationalError:
            # Fallback: simple LIKE search if FTS query syntax fails
            rows = self.conn.execute(
                """SELECT *, 0 as rank FROM nodes
                   WHERE (title LIKE ? OR content LIKE ?)
                     AND status != 'superseded'
                   ORDER BY weight DESC LIMIT ?""",
                (f"%{phrase}%", f"%{phrase}%", limit),
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

    # ── Stigmergic injection pheromone ──────────────────────────────────

    @staticmethod
    def _decayed_strength(strength: float, last_decay: str, half_life_days: float,
                          now: datetime | None = None) -> float:
        """Effective pheromone after exponential decay since last_decay."""
        if strength <= 0 or half_life_days <= 0:
            return max(0.0, strength)
        now = now or datetime.now()
        try:
            last = datetime.fromisoformat(last_decay)
        except (ValueError, TypeError):
            return strength
        days = (now - last).total_seconds() / 86400.0
        if days <= 0:
            return strength
        return strength * (0.5 ** (days / half_life_days))

    def _live_node_id(self, node_id: str, max_hops: int = 10) -> str:
        """Follow extra['superseded_by'] to the live successor (bounded, cycle-safe).

        Returns the input id unchanged when the node is live, missing, or the
        chain is malformed.
        """
        nid = node_id
        seen = {nid}
        for _ in range(max_hops):
            row = self.conn.execute(
                "SELECT extra FROM nodes WHERE id = ?", (nid,)).fetchone()
            if row is None:
                return nid
            try:
                extra = json.loads(row["extra"] or "{}")
            except (json.JSONDecodeError, TypeError):
                return nid
            succ = extra.get("superseded_by") if isinstance(extra, dict) else None
            if not succ or not isinstance(succ, str) or succ in seen:
                return nid
            seen.add(succ)
            nid = succ
        return nid

    def deposit_pheromone(self, node_id: str, context: str = "",
                          amount: float = 1.0, half_life_days: float = 14.0,
                          reinforce: bool = False, missed: bool = False) -> float:
        """Lay (or reinforce) pheromone on a node for a context.

        Folds prior decay into the stored strength before adding `amount`, so the
        accumulator stays current. Counter bumped depends on the kind of deposit:
        - default: `deposits` (a node was injected)
        - reinforce=True: `reinforcements` (a confirmed-useful injection)
        - missed=True: `missed` (counterfactual — would have helped but wasn't injected)
        Deposits on a superseded node are redirected to its live successor
        (supersede_node migrates existing trails exactly once; late deposits —
        e.g. deferred session-end reinforcement — must follow the same chain
        or the signal strands on the dead node and boosts it in ranking).
        Returns the new stored strength.
        """
        node_id = self._live_node_id(node_id)
        now = _now()
        d_inc = 0 if (reinforce or missed) else 1
        r_inc = 1 if reinforce else 0
        m_inc = 1 if missed else 0
        row = self.conn.execute(
            "SELECT strength, deposits, reinforcements, missed, last_decay "
            "FROM injection_pheromone WHERE node_id = ? AND context = ?",
            (node_id, context),
        ).fetchone()
        if row is None:
            self.conn.execute(
                "INSERT INTO injection_pheromone "
                "(node_id, context, strength, deposits, reinforcements, missed, last_deposit, last_decay) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (node_id, context, round(amount, 4), d_inc, r_inc, m_inc, now, now),
            )
            self.conn.commit()
            return round(amount, 4)

        decayed = self._decayed_strength(
            row["strength"], row["last_decay"], half_life_days)
        new_strength = round(decayed + amount, 4)
        self.conn.execute(
            "UPDATE injection_pheromone SET strength = ?, deposits = ?, "
            "reinforcements = ?, missed = ?, last_deposit = ?, last_decay = ? "
            "WHERE node_id = ? AND context = ?",
            (new_strength,
             row["deposits"] + d_inc,
             row["reinforcements"] + r_inc,
             row["missed"] + m_inc,
             now, now, node_id, context),
        )
        self.conn.commit()
        return new_strength

    def pheromone_scores(self, node_ids: set[str], context: str = "",
                         half_life_days: float = 14.0,
                         min_deposits: int = 5) -> list[tuple[str, float]]:
        """Decayed pheromone strength per node for retrieval ranking (read-only).

        Uses the context-conditioned trail when it has enough deposits to be
        statistically real; otherwise falls back to the coarse global trail.
        """
        if not node_ids:
            return []
        now = datetime.now()
        ids = list(node_ids)
        placeholders = ",".join("?" * len(ids))
        rows = self.conn.execute(
            f"SELECT node_id, context, strength, deposits, last_decay "
            f"FROM injection_pheromone "
            f"WHERE node_id IN ({placeholders}) AND context IN ('', ?)",
            (*ids, context),
        ).fetchall()

        conditioned: dict[str, sqlite3.Row] = {}
        glob: dict[str, sqlite3.Row] = {}
        for row in rows:
            (conditioned if row["context"] == context and context else glob)[row["node_id"]] = row

        results: list[tuple[str, float]] = []
        for nid in ids:
            cond = conditioned.get(nid)
            chosen = cond if (cond and cond["deposits"] >= min_deposits) else glob.get(nid)
            if chosen is None:
                continue
            strength = self._decayed_strength(
                chosen["strength"], chosen["last_decay"], half_life_days, now)
            if strength > 0.0:
                results.append((nid, strength))
        return results

    def decay_pheromone(self, half_life_days: float = 14.0,
                        floor: float = 0.02) -> int:
        """Write back decayed pheromone strength; prune trails below `floor`.

        Lazy decay already keeps reads correct; this periodic pass keeps the
        stored values honest and evaporates dead trails. Returns rows pruned.
        """
        now = datetime.now()
        rows = self.conn.execute(
            "SELECT node_id, context, strength, last_decay FROM injection_pheromone"
        ).fetchall()
        pruned = 0
        for row in rows:
            strength = self._decayed_strength(
                row["strength"], row["last_decay"], half_life_days, now)
            if strength < floor:
                self.conn.execute(
                    "DELETE FROM injection_pheromone WHERE node_id = ? AND context = ?",
                    (row["node_id"], row["context"]),
                )
                pruned += 1
            elif abs(strength - row["strength"]) > 0.001:
                self.conn.execute(
                    "UPDATE injection_pheromone SET strength = ?, last_decay = ? "
                    "WHERE node_id = ? AND context = ?",
                    (round(strength, 4), now.isoformat(timespec="seconds"),
                     row["node_id"], row["context"]),
                )
        self.conn.commit()
        return pruned

    def pheromone_stats(self, half_life_days: float = 14.0,
                        warm_floor: float = 0.5) -> dict:
        """Decayed signal summary used to decide if pheromone is mature enough
        to trust in ranking.

        Counts only GRADED signal (reinforcements or counterfactual deposits) —
        bare injection deposits are popularity, not usefulness, and are excluded.
        Uses decayed strength so the measure falls when trails cool.
        """
        now = datetime.now()
        rows = self.conn.execute(
            "SELECT node_id, context, strength, reinforcements, missed, last_decay "
            "FROM injection_pheromone WHERE reinforcements > 0 OR missed > 0"
        ).fetchall()
        warm_nodes: set[str] = set()
        warm_signal = 0.0
        signal_events = 0
        for row in rows:
            signal_events += (row["reinforcements"] or 0) + (row["missed"] or 0)
            strength = self._decayed_strength(
                row["strength"], row["last_decay"], half_life_days, now)
            if strength >= warm_floor:
                warm_nodes.add(row["node_id"])
                warm_signal += strength
        total_rows = self.conn.execute(
            "SELECT COUNT(*) FROM injection_pheromone").fetchone()[0]
        return {
            "warm_graded_nodes": len(warm_nodes),
            "warm_signal": round(warm_signal, 3),
            "signal_events": signal_events,
            "total_trails": total_rows,
        }

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

    def get_session_tags(
        self,
        status: str | None = None,
        project_path: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query session-tag nodes with optional status and project_path filters."""
        q = "SELECT * FROM nodes WHERE type = 'session' AND extra LIKE ?"
        params: list = ['%"session_status"%']
        if project_path:
            q += " AND extra LIKE ?"
            params.append(f'%"project_path"%"{project_path}"%')
        q += " ORDER BY updated_at DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(q, params).fetchall()
        results = [self._row_to_dict(r) for r in rows]
        if status:
            results = [
                r for r in results
                if (r.get("extra") or {}).get("session_status") == status
            ]
        return results

    def get_session_tag_by_name(self, tag_name: str) -> dict | None:
        """Find a session tag by its tag name in extra JSON."""
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE type = 'session' AND extra LIKE ?",
            (f'%"tag"%"{tag_name}"%',),
        ).fetchall()
        for r in rows:
            d = self._row_to_dict(r)
            if (d.get("extra") or {}).get("tag") == tag_name:
                return d
        return self.get_node_by_title(tag_name)

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
        """Update the current_state of a directive/operational node.

        Routed through atomic_extra_update so a concurrent extra writer
        (a lock, an expires edit, another set-state) is never clobbered
        by a stale-snapshot write.
        """
        node = self.get_node(node_id)
        if not node:
            return

        def _mutate(extra: dict) -> None:
            extra["current_state"] = state
            extra["state_updated_at"] = _now()

        self.atomic_extra_update(node_id, _mutate)
        self._log("update_state", node_id, node.get("title", ""),
                  details={"state": state})

    # ── Reminders ───────────────────────────────────────────────────────

    def add_reminder(
        self,
        title: str,
        next_due: str,
        *,
        reminder_id: str | None = None,
        body: str = "",
        priority: str = "normal",
        reminder_type: str = "once",
        schedule: str = "",
        channels: list[str] | None = None,
        related_node_id: str | None = None,
        tags: str = "",
        extra: dict | None = None,
    ) -> str:
        """Insert a reminder. Returns its ID."""
        rid = reminder_id or _uuid()
        now = _now()
        self.conn.execute(
            """INSERT INTO reminders
               (id, title, body, priority, status, reminder_type, schedule,
                next_due, channels, related_node_id, tags, extra,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (rid, title, body, priority, reminder_type, schedule,
             next_due, _jdumps(channels or []), related_node_id or "",
             tags, _jdumps(extra or {}), now, now),
        )
        self.conn.commit()
        self._log("add_reminder", rid, title,
                  details={"priority": priority, "next_due": next_due,
                           "type": reminder_type})
        return rid

    def get_reminder(self, reminder_id: str) -> dict | None:
        """Fetch a reminder by ID."""
        row = self.conn.execute(
            "SELECT * FROM reminders WHERE id = ?", (reminder_id,)
        ).fetchone()
        if row is None:
            return None
        return self._reminder_to_dict(row)

    def update_reminder(self, reminder_id: str, **fields) -> None:
        """Update specific fields on a reminder."""
        allowed = {
            "title", "body", "priority", "status", "reminder_type",
            "schedule", "next_due", "last_fired", "snooze_until",
            "snooze_count", "channels", "related_node_id", "tags", "extra",
        }
        updates = []
        values = []
        for k, v in fields.items():
            if k not in allowed:
                continue
            if k in ("channels", "extra"):
                v = _jdumps(v)
            updates.append(f"{k} = ?")
            values.append(v)
        if not updates:
            return
        updates.append("updated_at = ?")
        values.append(_now())
        values.append(reminder_id)
        self.conn.execute(
            f"UPDATE reminders SET {', '.join(updates)} WHERE id = ?",
            values,
        )
        self.conn.commit()

    def delete_reminder(self, reminder_id: str) -> None:
        """Delete a reminder."""
        self.conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        self.conn.commit()
        self._log("delete_reminder", reminder_id)

    def list_reminders(
        self,
        status: str | None = None,
        priority: str | None = None,
        limit: int = 50,
    ) -> list[dict]:
        """List reminders with optional filters."""
        clauses = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if priority:
            clauses.append("priority = ?")
            params.append(priority)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self.conn.execute(
            f"""SELECT * FROM reminders{where}
                ORDER BY
                    CASE priority
                        WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                        WHEN 'normal' THEN 2 WHEN 'low' THEN 3
                    END,
                    next_due ASC
                LIMIT ?""",
            params,
        ).fetchall()
        return [self._reminder_to_dict(r) for r in rows]

    def due_reminders(self, as_of: str | None = None) -> list[dict]:
        """Get all reminders that are due now (active past due or snoozed past snooze_until)."""
        now = as_of or _now()
        rows = self.conn.execute(
            """SELECT * FROM reminders
               WHERE (status = 'active' AND next_due <= ?)
                  OR (status = 'snoozed' AND snooze_until <= ?)
               ORDER BY
                   CASE priority
                       WHEN 'urgent' THEN 0 WHEN 'high' THEN 1
                       WHEN 'normal' THEN 2 WHEN 'low' THEN 3
                   END,
                   next_due ASC""",
            (now, now),
        ).fetchall()
        return [self._reminder_to_dict(r) for r in rows]

    def snooze_reminder(
        self, reminder_id: str, snooze_until: str, increment_count: bool = True,
    ) -> None:
        """Set a reminder to snoozed status."""
        fields: dict[str, Any] = {
            "status": "snoozed",
            "snooze_until": snooze_until,
        }
        if increment_count:
            r = self.get_reminder(reminder_id)
            if r:
                fields["snooze_count"] = r.get("snooze_count", 0) + 1
        self.update_reminder(reminder_id, **fields)
        self._log("snooze_reminder", reminder_id, "",
                  details={"snooze_until": snooze_until})

    def complete_reminder(self, reminder_id: str) -> None:
        """Mark a reminder as completed."""
        self.update_reminder(reminder_id, status="completed")
        self._log("complete_reminder", reminder_id)

    def _reminder_to_dict(self, row: sqlite3.Row) -> dict:
        """Convert a reminder row to dict with JSON parsing."""
        d = dict(row)
        for key in ("channels", "extra"):
            if key in d and isinstance(d[key], str):
                try:
                    d[key] = json.loads(d[key])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d

    def nearest_pending_reminder(self) -> str | None:
        """Return the ISO timestamp of the nearest pending reminder, or None."""
        row = self.conn.execute(
            """SELECT MIN(CASE
                 WHEN status = 'active' THEN next_due
                 WHEN status = 'snoozed' THEN snooze_until
               END) AS nearest
               FROM reminders
               WHERE status IN ('active', 'snoozed')"""
        ).fetchone()
        if row is None or row["nearest"] is None:
            return None
        return row["nearest"]

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
        d["tags"] = d.get("domains") or []
        return d

    def node_ids(self) -> list[str]:
        """All node IDs."""
        return [r[0] for r in self.conn.execute("SELECT id FROM nodes").fetchall()]
