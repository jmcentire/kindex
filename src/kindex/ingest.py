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
        "wanderrepos": "research",
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

        # Create session node
        summary = text[:500]
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


def resolve_kin_chain(kin_path: Path, max_depth: int = 5) -> list[dict]:
    """Resolve the .kin inheritance chain for a given .kin file.

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
