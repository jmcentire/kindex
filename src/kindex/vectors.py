"""Optional vector embedding support via sqlite-vec.

Enables semantic similarity search when installed:
    pip install sqlite-vec sentence-transformers

Falls back gracefully to FTS5 when unavailable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config
    from .store import Store

_VEC_AVAILABLE = None
_MODEL = None
_EMBEDDING_DIM = 384  # all-MiniLM-L6-v2 default


def _check_vec() -> bool:
    """Check if sqlite-vec extension is available."""
    global _VEC_AVAILABLE
    if _VEC_AVAILABLE is not None:
        return _VEC_AVAILABLE
    try:
        import sqlite_vec  # noqa: F401
        _VEC_AVAILABLE = True
    except ImportError:
        _VEC_AVAILABLE = False
    return _VEC_AVAILABLE


def _get_model():
    """Lazy-load the sentence transformer model."""
    global _MODEL
    if _MODEL is not None:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer("all-MiniLM-L6-v2")
        return _MODEL
    except ImportError:
        print("Warning: sentence-transformers not installed. "
              "Install with: pip install sentence-transformers", file=sys.stderr)
        return None


def is_available() -> bool:
    """Check if vector search is available."""
    return _check_vec()


def embed_text(text: str) -> list[float] | None:
    """Embed a text string into a vector."""
    model = _get_model()
    if model is None:
        return None
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def ensure_vec_table(store: Store) -> bool:
    """Create the vector table if sqlite-vec is available."""
    if not _check_vec():
        return False

    try:
        import sqlite_vec
        store.conn.enable_load_extension(True)
        sqlite_vec.load(store.conn)
        store.conn.enable_load_extension(False)

        store.conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS node_vectors USING vec0(
                node_id TEXT PRIMARY KEY,
                embedding float[{_EMBEDDING_DIM}]
            )
        """)
        store.conn.commit()
        return True
    except Exception as e:
        print(f"Warning: Could not initialize vector table: {e}", file=sys.stderr)
        return False


def upsert_embedding(store: Store, node_id: str, text: str) -> bool:
    """Compute and store an embedding for a node."""
    embedding = embed_text(text)
    if embedding is None:
        return False

    try:
        store.conn.execute(
            "INSERT OR REPLACE INTO node_vectors (node_id, embedding) VALUES (?, ?)",
            (node_id, _serialize_vec(embedding)),
        )
        store.conn.commit()
        return True
    except Exception:
        return False


def vector_search(store: Store, query: str, top_k: int = 10) -> list[dict]:
    """Search for similar nodes using vector similarity."""
    if not _check_vec():
        return []

    embedding = embed_text(query)
    if embedding is None:
        return []

    try:
        rows = store.conn.execute(
            """SELECT node_id, distance
               FROM node_vectors
               WHERE embedding MATCH ?
               ORDER BY distance
               LIMIT ?""",
            (_serialize_vec(embedding), top_k),
        ).fetchall()

        results = []
        for row in rows:
            node = store.get_node(row[0])
            if node:
                node["vec_distance"] = row[1]
                results.append(node)
        return results
    except Exception:
        return []


def _serialize_vec(embedding: list[float]) -> bytes:
    """Serialize a float list to bytes for sqlite-vec."""
    import struct
    return struct.pack(f"{len(embedding)}f", *embedding)


def index_all_nodes(store: Store, verbose: bool = False) -> int:
    """Index all nodes that don't have embeddings yet."""
    if not ensure_vec_table(store):
        print("Vector search not available. Install: pip install sqlite-vec sentence-transformers",
              file=sys.stderr)
        return 0

    nodes = store.all_nodes(limit=10000)
    count = 0
    for node in nodes:
        text = f"{node['title']} {(node.get('content') or '')[:1000]}"
        if upsert_embedding(store, node["id"], text):
            count += 1
            if verbose:
                print(f"  Embedded: {node['title']}")

    return count
