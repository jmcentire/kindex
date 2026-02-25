"""Ingestion — scan projects, sessions, and external sources into the graph."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store


# ── Project scanning ──────────────────────────────────────────────────


def scan_projects(config: Config, store: Store, verbose: bool = False) -> int:
    """Scan configured directories for repos with CLAUDE.md files.

    Creates/updates project nodes and extracts key context from each.
    Returns count of new nodes created.
    """
    count = 0

    for project_dir in config.resolved_project_dirs:
        if not project_dir.exists():
            continue

        # Find CLAUDE.md files (direct children only — one per project)
        for claude_md in sorted(project_dir.rglob("CLAUDE.md")):
            # The project root is the parent of CLAUDE.md
            project_root = claude_md.parent
            slug = _project_slug(project_root)

            # Skip if already exists and was recently scanned
            existing = store.get_node(slug)
            if existing:
                # Update content if CLAUDE.md changed
                content = _extract_project_context(claude_md)
                if content != (existing.get("content") or ""):
                    store.update_node(slug, content=content,
                                      extra={"path": str(project_root)})
                    if verbose:
                        print(f"  Updated: {slug}")
                continue

            content = _extract_project_context(claude_md)
            title = _infer_title(project_root, claude_md)
            audience = _infer_audience(project_root)

            store.add_node(
                node_id=slug,
                title=title,
                content=content,
                node_type="project",
                domains=_infer_domains(project_root),
                audience=audience,
                prov_source=str(claude_md),
                prov_activity="project-scan",
                extra={"path": str(project_root)},
            )
            count += 1
            if verbose:
                print(f"  Created: {title} ({slug})")

            # Auto-link to existing nodes via keyword matching
            _auto_link_project(store, slug, content)

    return count


def _project_slug(project_root: Path) -> str:
    """Generate a stable slug from a project path."""
    # Use last two path components for uniqueness
    parts = project_root.parts
    if len(parts) >= 2:
        return f"proj-{parts[-2]}-{parts[-1]}".lower().replace(" ", "-")
    return f"proj-{parts[-1]}".lower().replace(" ", "-")


def _infer_title(project_root: Path, claude_md: Path) -> str:
    """Infer project title from CLAUDE.md or directory name."""
    try:
        text = claude_md.read_text(errors="replace")[:2000]
        # Look for a markdown heading
        for line in text.split("\n"):
            line = line.strip()
            if line.startswith("# ") and len(line) > 3:
                return line[2:].strip()
    except OSError:
        pass
    return project_root.name.replace("-", " ").replace("_", " ").title()


def _extract_project_context(claude_md: Path) -> str:
    """Extract the key content from a CLAUDE.md file."""
    try:
        text = claude_md.read_text(errors="replace")
        # Limit to reasonable size but keep the important stuff
        if len(text) > 4000:
            text = text[:4000] + "\n...(truncated)"
        return text.strip()
    except OSError:
        return ""


def _infer_domains(project_root: Path) -> list[str]:
    """Infer domains from project structure."""
    domains = []
    indicators = {
        "pyproject.toml": "python",
        "package.json": "javascript",
        "Cargo.toml": "rust",
        "go.mod": "go",
        "pom.xml": "java",
        "Gemfile": "ruby",
    }
    for filename, domain in indicators.items():
        if (project_root / filename).exists():
            domains.append(domain)

    # Check parent dir for broader domain hints
    parent_name = project_root.parent.name.lower()
    domain_map = {
        "code": "engineering",
        "personal": "personal",
        "research": "research",
        "work": "work",
    }
    if parent_name in domain_map:
        domains.append(domain_map[parent_name])

    return domains


def _auto_link_project(store: Store, project_slug: str, content: str) -> None:
    """Link a project node to existing nodes that appear in its content."""
    content_lower = content.lower()
    existing = store.all_nodes(limit=500)

    for node in existing:
        if node["id"] == project_slug:
            continue
        title_lower = node["title"].lower()
        # Link if the project content mentions an existing node title
        if len(title_lower) > 3 and title_lower in content_lower:
            store.add_edge(
                project_slug, node["id"],
                edge_type="context_of",
                weight=0.4,
                provenance="auto-linked from CLAUDE.md content",
                bidirectional=True,
            )


# ── Session learning ──────────────────────────────────────────────────


def scan_sessions(
    config: Config,
    store: Store,
    limit: int = 10,
    verbose: bool = False,
) -> int:
    """Scan Claude Code project directories for session data.

    Reads conversation JSONL files from ~/.claude/projects/ and extracts
    knowledge into the graph.

    Returns count of new nodes created.
    """
    projects_dir = config.claude_path / "projects"
    if not projects_dir.exists():
        return 0

    count = 0
    from .extract import keyword_extract

    # Try to set up LLM summarization
    llm_summarize = None
    try:
        from .extract import llm_summarize_session
        from .budget import BudgetLedger
        ledger = BudgetLedger(config.ledger_path, config.budget)
        if ledger.can_spend():
            llm_summarize = lambda txt: llm_summarize_session(txt, config, ledger)
    except Exception:
        pass

    # Find recent JSONL conversation files
    jsonl_files = sorted(
        projects_dir.rglob("*.jsonl"),
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )[:limit]

    for jsonl_path in jsonl_files:
        session_id = jsonl_path.stem[:12]
        session_slug = f"session-{session_id}"

        # Skip already-ingested sessions
        if store.get_node(session_slug):
            continue

        # Extract text from the session
        text = _extract_session_text(jsonl_path, max_chars=8000)
        if not text or len(text) < 50:
            continue

        # Determine which project this session belongs to
        project_context = jsonl_path.parent.name

        # Extract knowledge
        extraction = keyword_extract(text)
        concepts = extraction.get("concepts", [])

        if not concepts:
            continue

        # Generate summary: LLM if available, fallback to first 500 chars
        summary = None
        if llm_summarize is not None:
            try:
                summary = llm_summarize(text)
            except Exception:
                pass
        if not summary:
            summary = text[:500]

        # Create session node
        store.add_node(
            node_id=session_slug,
            title=f"Session: {project_context[:40]}",
            content=summary,
            node_type="session",
            prov_source=str(jsonl_path),
            prov_activity="session-scan",
            extra={"project": project_context},
        )
        count += 1

        if verbose:
            print(f"  Session: {session_slug} ({project_context})")

        # Link extracted concepts
        for concept in concepts[:5]:
            existing = store.get_node_by_title(concept["title"])
            if existing:
                store.add_edge(
                    session_slug, existing["id"],
                    edge_type="context_of",
                    weight=0.3,
                    provenance="mentioned in session",
                )

        # Link to project node if it exists
        _link_session_to_project(store, session_slug, project_context)

    return count


def _extract_session_text(jsonl_path: Path, max_chars: int = 8000) -> str:
    """Extract human-readable text from a Claude Code JSONL session file."""
    texts = []
    total_len = 0

    try:
        with open(jsonl_path, "r", errors="replace") as f:
            for line in f:
                if total_len >= max_chars:
                    break
                try:
                    entry = json.loads(line)
                except (json.JSONDecodeError, ValueError):
                    continue

                # Extract text from assistant messages
                role = entry.get("role", "")
                if role != "assistant":
                    continue

                content = entry.get("content", "")
                if isinstance(content, str):
                    texts.append(content[:1000])
                    total_len += len(content[:1000])
                elif isinstance(content, list):
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            text = block.get("text", "")[:1000]
                            texts.append(text)
                            total_len += len(text)
    except OSError:
        return ""

    return "\n".join(texts)


# ── Parent directory .kin walk ────────────────────────────────────────


def find_parent_kin(start_path: Path | None = None, max_depth: int = 10) -> list[Path]:
    """Walk up from start_path (or cwd) to find .kin files.

    Returns list of .kin file paths from most specific (deepest) to root.
    Stops at filesystem root or after max_depth levels.
    """
    if start_path is None:
        start_path = Path.cwd()
    start_path = Path(start_path).resolve()

    found = []
    current = start_path
    for _ in range(max_depth):
        kin_file = current / ".kin"
        if kin_file.exists():
            found.append(kin_file)
        parent = current.parent
        if parent == current:  # filesystem root
            break
        current = parent

    return found


# ── .kin file support ────────────────────────────────────────────────


def scan_kin_files(config: Config, store: Store, verbose: bool = False) -> int:
    """Scan project directories for .kin files (per-repo metadata).

    A .kin file in a repo root provides Kindex-specific metadata:
      audience: team
      domains: [engineering, python]
      connects_to: [other-project-slug]
      description: What this project is about.

    Returns count of updated nodes.
    """
    import yaml

    count = 0
    for project_dir in config.resolved_project_dirs:
        if not project_dir.exists():
            continue

        for conv_file in sorted(project_dir.rglob(".kin")):
            project_root = conv_file.parent
            slug = _project_slug(project_root)

            try:
                data = yaml.safe_load(conv_file.read_text()) or {}
            except Exception:
                continue

            existing = store.get_node(slug)
            if existing:
                # Update from .conv metadata
                updates = {}
                if "audience" in data:
                    updates["audience"] = data["audience"]
                if "domains" in data:
                    updates["domains"] = data["domains"]
                if "description" in data:
                    updates["content"] = data["description"]
                if updates:
                    store.update_node(slug, **updates)
                    count += 1
                    if verbose:
                        print(f"  Updated from .kin: {slug}")

                # Handle connects_to
                for target in data.get("connects_to", []):
                    target_node = store.get_node(target) or store.get_node_by_title(target)
                    if target_node:
                        store.add_edge(slug, target_node["id"],
                                       edge_type="relates_to",
                                       weight=0.6,
                                       provenance=".kin file")
            else:
                # Create from .conv if CLAUDE.md scan missed it
                title = data.get("title", project_root.name.replace("-", " ").title())
                audience = data.get("audience", _infer_audience(project_root))
                store.add_node(
                    node_id=slug,
                    title=title,
                    content=data.get("description", ""),
                    node_type="project",
                    domains=data.get("domains", _infer_domains(project_root)),
                    audience=audience,
                    prov_source=str(conv_file),
                    prov_activity="kin-file-scan",
                    extra={"path": str(project_root)},
                )
                count += 1
                if verbose:
                    print(f"  Created from .kin: {slug}")

    return count


# ── .kin inheritance resolution ───────────────────────────────────────


def resolve_kin_chain(kin_path: Path, max_depth: int = 5, auto_walk: bool = False) -> list[dict]:
    """Resolve the .kin inheritance chain for a given .kin file.

    If auto_walk=True and no explicit inherits, also walk up the directory tree
    to discover parent .kin files automatically.

    Returns a list of parsed .kin dicts from most specific (local) to
    most general (root ancestor). Each inheritor's values override ancestors.

    The .kin file format:
        name: my-project
        audience: team
        inherits:
          - ../platform/.kin       # parent repo context
          - ~/.kindex/.kin         # user's personal kindex (private)
        shared_with:
          - team: engineering
        privacy: team
    """
    import yaml

    chain = []
    visited = set()
    _resolve_kin_recursive(kin_path, chain, visited, max_depth)

    # If auto_walk is enabled and the root .kin file has no explicit inherits,
    # walk up the directory tree for additional .kin files
    if auto_walk and chain:
        root_data = chain[0]
        if not root_data.get("inherits"):
            parent_kins = find_parent_kin(kin_path.parent.parent, max_depth=max_depth)
            for parent_kin in parent_kins:
                resolved = parent_kin.resolve()
                if str(resolved) not in visited:
                    _resolve_kin_recursive(parent_kin, chain, visited, max_depth - len(chain))

    return chain


def _resolve_kin_recursive(
    kin_path: Path,
    chain: list[dict],
    visited: set,
    remaining: int,
) -> None:
    """Recursively resolve .kin inheritance chain."""
    import yaml

    resolved = kin_path.expanduser().resolve()
    if str(resolved) in visited or remaining <= 0:
        return
    visited.add(str(resolved))

    if not resolved.exists():
        return

    try:
        data = yaml.safe_load(resolved.read_text()) or {}
    except Exception:
        return

    data["_source"] = str(resolved)
    chain.append(data)

    # Resolve inherited .kin files
    for parent_ref in data.get("inherits", []):
        parent_path = (resolved.parent / parent_ref).resolve()
        _resolve_kin_recursive(parent_path, chain, visited, remaining - 1)


def merge_kin_chain(chain: list[dict]) -> dict:
    """Merge an inheritance chain into a single resolved config.

    Later entries (ancestors) provide defaults; earlier entries (local) override.
    Lists are concatenated. Dicts are merged (local wins). Scalars are overridden.
    """
    if not chain:
        return {}

    result: dict = {}

    # Process from ancestor (end) to local (start) — local overrides
    for layer in reversed(chain):
        for key, value in layer.items():
            if key.startswith("_"):
                continue
            if key == "inherits":
                continue
            if key in result:
                existing = result[key]
                if isinstance(existing, list) and isinstance(value, list):
                    # Concatenate lists, dedup
                    seen = set()
                    merged = []
                    for item in value + existing:
                        s = str(item)
                        if s not in seen:
                            seen.add(s)
                            merged.append(item)
                    result[key] = merged
                elif isinstance(existing, dict) and isinstance(value, dict):
                    existing.update(value)
                else:
                    result[key] = value
            else:
                result[key] = value

    result["_chain"] = [d.get("_source", "?") for d in chain]
    return result


def load_project_context(kin_path: Path) -> dict:
    """Load the full resolved context for a project from its .kin file.

    This is the main entry point for Claude Code integration.
    When Claude opens a repo, it calls this to get the merged context
    from the full inheritance chain.
    """
    chain = resolve_kin_chain(kin_path)
    return merge_kin_chain(chain)


def _infer_audience(project_root: Path) -> str:
    """Infer audience from directory heuristic."""
    path_str = str(project_root).lower()
    if "/personal/" in path_str or "/gemweaver/" in path_str:
        return "private"
    if "/work/" in path_str:
        return "team"
    if "/code/" in path_str:
        return "team"  # code repos default to team-visible
    return "private"


# ── Synonym ring files ───────────────────────────────────────────────


def load_synonym_rings(config: "Config", store: "Store", verbose: bool = False) -> int:
    """Load synonym ring files from the data directory.

    Synonym ring format (.syn files in data_dir/synonyms/):
        ring: database-terms
        synonyms:
          - database
          - db
          - datastore
          - data store
          - persistence layer

    Applies synonyms as AKA entries on matching nodes.
    Returns count of nodes updated.
    """
    import yaml

    syn_dir = config.data_path / "synonyms"
    if not syn_dir.exists():
        return 0

    count = 0
    for syn_file in sorted(syn_dir.glob("*.syn")):
        try:
            data = yaml.safe_load(syn_file.read_text()) or {}
        except Exception:
            if verbose:
                print(f"  Warning: could not parse {syn_file.name}")
            continue

        ring_name = data.get("ring", syn_file.stem)
        synonyms = data.get("synonyms", [])
        if not synonyms or not isinstance(synonyms, list):
            continue

        # For each synonym, find nodes whose title matches and add
        # all other synonyms as AKA entries
        for synonym in synonyms:
            node = store.get_node_by_title(synonym)
            if not node:
                continue

            existing_aka = list(node.get("aka") or [])
            new_aka = list(existing_aka)
            added = False

            for other in synonyms:
                if other == synonym:
                    continue
                if other.lower() == node.get("title", "").lower():
                    continue
                if other not in new_aka:
                    new_aka.append(other)
                    added = True

            if added:
                store.update_node(node["id"], aka=new_aka)
                count += 1
                if verbose:
                    added_count = len(new_aka) - len(existing_aka)
                    print(f"  {node['title']}: +{added_count} synonyms from ring '{ring_name}'")

    return count


def _link_session_to_project(store: Store, session_slug: str, project_context: str) -> None:
    """Try to link a session to its corresponding project node."""
    # project_context is like "-Users-jmcentire-Code-Conv"
    # Try to match to a project node
    parts = project_context.strip("-").split("-")
    # Try from the end, building longer matches
    for i in range(len(parts), max(0, len(parts) - 3), -1):
        candidate = "-".join(parts[-2:]).lower() if len(parts) >= 2 else parts[-1].lower()
        slug = f"proj-{candidate}"
        if store.get_node(slug):
            store.add_edge(
                session_slug, slug,
                edge_type="spawned_from",
                weight=0.5,
                provenance="session in project dir",
            )
            return


# ── Person expertise auto-detection ───────────────────────────────────


def detect_expertise(store: "Store", person_node_id: str) -> dict[str, int]:
    """Detect expertise domains for a person by analysing their graph connections.

    Walks edges from the person node, inspects the ``domains`` field on
    each connected node, and tallies how often each domain appears.  The
    person's node is then updated with the top domains.

    Parameters
    ----------
    store : Store
        An open Kindex store.
    person_node_id : str
        The node ID of the person to analyse.

    Returns
    -------
    dict[str, int]
        Mapping of domain name to frequency count.
    """
    from collections import Counter

    person = store.get_node(person_node_id)
    if person is None:
        return {}

    domain_counts: Counter[str] = Counter()

    # 1. Tally domains from directly connected nodes
    edges = store.edges_from(person_node_id)
    for edge in edges:
        target_id = edge.get("to_id")
        if not target_id:
            continue
        target = store.get_node(target_id)
        if target is None:
            continue
        for domain in (target.get("domains") or []):
            domain_counts[domain] += 1

    # 2. Also inspect activity log entries for this person
    activities = store.activity_by_actor(person_node_id, limit=100)
    for act in activities:
        node_id = act.get("node_id") or ""
        if not node_id:
            continue
        node = store.get_node(node_id)
        if node is None:
            continue
        for domain in (node.get("domains") or []):
            domain_counts[domain] += 1

    # 3. Update the person node with the top domains (up to 10)
    if domain_counts:
        top_domains = [d for d, _ in domain_counts.most_common(10)]
        store.update_node(person_node_id, domains=top_domains)

    return dict(domain_counts)


# ── Git-tracked index ────────────────────────────────────────────────


def _now_iso() -> str:
    """Return current timestamp in ISO format."""
    from datetime import datetime
    return datetime.now(tz=None).isoformat(timespec="seconds")


def write_kin_index(store: "Store", output_dir: Path) -> Path:
    """Write a .kin/index.json file summarizing the graph for this project.

    This file is meant to be git-tracked, giving other tools a snapshot
    of what Kindex knows about this project.
    """
    nodes = store.all_nodes(limit=500)
    index = {
        "version": 1,
        "generated_at": _now_iso(),
        "node_count": len(nodes),
        "nodes": [
            {"id": n["id"], "title": n["title"], "type": n["type"],
             "weight": n["weight"], "domains": n.get("domains", [])}
            for n in nodes[:100]  # Top 100 by weight
        ],
        "domains": list(set(d for n in nodes for d in (n.get("domains") or []))),
    }

    output_path = output_dir / ".kin" / "index.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(index, indent=2) + "\n")
    return output_path
