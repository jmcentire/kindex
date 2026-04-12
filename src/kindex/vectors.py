"""Optional vector embedding support via sqlite-vec.

Enables semantic similarity search when installed:
    pip install kindex[vectors]          # just sqlite-vec; API-based providers work out of the box
    pip install sentence-transformers    # opt-in for local embeddings (pulls torch + sklearn)

Supports multiple embedding providers:
    - voyage: Voyage AI Embeddings API (requires VOYAGE_API_KEY) — recommended default
    - openai: OpenAI Embeddings API (requires OPENAI_API_KEY)
    - gemini: Google Gemini Embeddings API (requires GEMINI_API_KEY)
    - local: sentence-transformers (requires separate pip install sentence-transformers)

Falls back gracefully to FTS5 when the configured provider is unavailable.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .config import Config, EmbeddingConfig
    from .store import Store

_VEC_AVAILABLE = None
_MODEL = None

# Provider defaults: model, dimensions, api_key_env
PROVIDER_DEFAULTS = {
    "local": {"model": "all-MiniLM-L6-v2", "dimensions": 384, "api_key_env": ""},
    "openai": {"model": "text-embedding-3-small", "dimensions": 1536, "api_key_env": "OPENAI_API_KEY"},
    "gemini": {"model": "gemini-embedding-001", "dimensions": 3072, "api_key_env": "GEMINI_API_KEY"},
    "voyage": {"model": "voyage-3.5", "dimensions": 1024, "api_key_env": "VOYAGE_API_KEY"},
}


def _resolve_embedding_config(config: Config | None) -> tuple[str, str, int, str]:
    """Resolve provider, model, dimensions, api_key_env from config.

    Returns (provider, model, dimensions, api_key_env).
    """
    if config is None:
        defaults = PROVIDER_DEFAULTS["local"]
        return "local", defaults["model"], defaults["dimensions"], ""

    ec = config.embedding
    provider = ec.provider
    defaults = PROVIDER_DEFAULTS.get(provider, PROVIDER_DEFAULTS["local"])
    model = ec.model or defaults["model"]
    dims = ec.dimensions or defaults["dimensions"]
    api_key_env = ec.api_key_env or defaults["api_key_env"]
    return provider, model, dims, api_key_env


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


def _get_model(model_name: str = "all-MiniLM-L6-v2"):
    """Lazy-load the sentence transformer model."""
    global _MODEL
    if _MODEL is not None and getattr(_MODEL, '_kindex_model_name', None) == model_name:
        return _MODEL
    try:
        from sentence_transformers import SentenceTransformer
        _MODEL = SentenceTransformer(model_name)
        _MODEL._kindex_model_name = model_name
        return _MODEL
    except ImportError:
        print("Warning: sentence-transformers not installed. "
              "Install with: pip install sentence-transformers", file=sys.stderr)
        return None


def _embed_local(text: str, model_name: str) -> list[float] | None:
    """Embed text using local sentence-transformers."""
    model = _get_model(model_name)
    if model is None:
        return None
    embedding = model.encode(text, normalize_embeddings=True)
    return embedding.tolist()


def _embed_openai(text: str, model: str, dimensions: int, api_key_env: str) -> list[float] | None:
    """Embed text using OpenAI Embeddings API."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"Warning: {api_key_env} not set. Cannot embed text.", file=sys.stderr)
        return None

    body = {"input": text, "model": model}
    if dimensions:
        body["dimensions"] = dimensions

    try:
        req = urllib.request.Request(
            "https://api.openai.com/v1/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result["data"][0]["embedding"]
    except Exception as e:
        print(f"OpenAI embedding error: {e}", file=sys.stderr)
        return None


def _embed_gemini(text: str, model: str, dimensions: int, api_key_env: str) -> list[float] | None:
    """Embed text using Google Gemini Embeddings API."""
    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"Warning: {api_key_env} not set. Cannot embed text.", file=sys.stderr)
        return None

    body = {
        "model": f"models/{model}",
        "content": {"parts": [{"text": text}]},
    }
    if dimensions:
        body["outputDimensionality"] = dimensions

    try:
        req = urllib.request.Request(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:embedContent",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            if "embedding" in result and "values" in result["embedding"]:
                return result["embedding"]["values"]
            return None
    except Exception as e:
        print(f"Gemini embedding error: {e}", file=sys.stderr)
        return None


def _embed_voyage(text: str, model: str, dimensions: int, api_key_env: str) -> list[float] | None:
    """Embed text using Voyage AI Embeddings API.

    Voyage is Anthropic's recommended embeddings provider. Ships as a pure-HTTP
    API with no native dependencies. Supports general-purpose and domain-specific
    models (voyage-3.5 default, voyage-finance-2, voyage-law-2, voyage-code-3).
    Free tier: 200M tokens for voyage-3.5 / voyage-3-large / voyage-3.5-lite.
    """
    api_key = os.environ.get(api_key_env)
    if not api_key:
        print(f"Warning: {api_key_env} not set. Cannot embed text.", file=sys.stderr)
        return None

    body = {
        "input": [text],
        "model": model,
        "input_type": "document",
    }

    try:
        req = urllib.request.Request(
            "https://api.voyageai.com/v1/embeddings",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {api_key}",
            },
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = json.loads(resp.read().decode())
            return result["data"][0]["embedding"]
    except Exception as e:
        print(f"Voyage embedding error: {e}", file=sys.stderr)
        return None


_EMBED_DISPATCH = {
    "local": lambda text, model, dims, key_env: _embed_local(text, model),
    "openai": _embed_openai,
    "gemini": _embed_gemini,
    "voyage": _embed_voyage,
}


def is_available() -> bool:
    """Check if vector search is available."""
    return _check_vec()


def embed_text(text: str, config: Config | None = None) -> list[float] | None:
    """Embed a text string into a vector using the configured provider."""
    provider, model, dims, api_key_env = _resolve_embedding_config(config)
    fn = _EMBED_DISPATCH.get(provider)
    if fn is None:
        print(f"Warning: unknown embedding provider '{provider}'. "
              f"Supported: {', '.join(PROVIDER_DEFAULTS)}", file=sys.stderr)
        return None
    return fn(text, model, dims, api_key_env)


def _get_embedding_dim(config: Config | None) -> int:
    """Get the embedding dimension for the configured provider."""
    _, _, dims, _ = _resolve_embedding_config(config)
    return dims


def ensure_vec_table(store: Store) -> bool:
    """Create the vector table if sqlite-vec is available.

    Handles dimension changes from provider switches by recreating the table.
    """
    if not _check_vec():
        return False

    dim = _get_embedding_dim(store.config)

    try:
        import sqlite_vec
        store.conn.enable_load_extension(True)
        sqlite_vec.load(store.conn)
        store.conn.enable_load_extension(False)

        # Check if dimension changed (provider switch)
        store.conn.execute(
            "CREATE TABLE IF NOT EXISTS vec_meta (key TEXT PRIMARY KEY, value TEXT)"
        )
        row = store.conn.execute(
            "SELECT value FROM vec_meta WHERE key = 'embedding_dim'"
        ).fetchone()
        stored_dim = int(row[0]) if row else None

        if stored_dim and stored_dim != dim:
            print(f"Embedding dimension changed ({stored_dim} -> {dim}). "
                  f"Recreating vector table.", file=sys.stderr)
            store.conn.execute("DROP TABLE IF EXISTS node_vectors")

        store.conn.execute(f"""
            CREATE VIRTUAL TABLE IF NOT EXISTS node_vectors USING vec0(
                node_id TEXT PRIMARY KEY,
                embedding float[{dim}]
            )
        """)
        store.conn.execute(
            "INSERT OR REPLACE INTO vec_meta (key, value) VALUES ('embedding_dim', ?)",
            (str(dim),),
        )
        store.conn.commit()
        return True
    except Exception as e:
        print(f"Warning: Could not initialize vector table: {e}", file=sys.stderr)
        return False


def upsert_embedding(store: Store, node_id: str, text: str) -> bool:
    """Compute and store an embedding for a node."""
    embedding = embed_text(text, store.config)
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

    embedding = embed_text(query, store.config)
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
        provider = "unknown"
        try:
            provider, _, _, _ = _resolve_embedding_config(store.config)
        except Exception:
            pass
        if provider == "local":
            print("Vector search not available. Install: pip install sqlite-vec sentence-transformers",
                  file=sys.stderr)
        else:
            print("Vector search not available. Install: pip install sqlite-vec",
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
