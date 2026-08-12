# Oracle Contract — run v8

**Status: authored retroactively (2026-08-12), after the run closed.** It did not
gate this run. It exists because `harness/phase1_gate.sh` now requires it, v8
itself fails that gate for its absence, and the run produced the concrete evidence
of why it should exist. Treat this as the exemplar for the next run, not as a
Phase-A artifact of this one.

## Why this artifact exists

The Tester's projection contains no implementation source, by design — an oracle
that reads the implementation is not independent evidence. But the oracle must
still *call* things, and anything it calls is a contract. In v8 the Tester hit
three Phase-A contract defects and one projection defect, **all discovered after
dispatch**, each costing a stall and a Validator round trip:

1. a test-visible signature it could not see and had to guess,
2. a return shape (tuple vs object) it guessed wrong,
3. a marker registration location that no artifact declared,
4. a strategy that referenced paths outside its declared view.

Every input needed to prevent all four existed before launch. Nothing compared
them. Item 4 is now enforced by `harness/projection_receipt.sh`; items 1–3 are
what this document is for.

**Rule: if the oracle must call it, name it here. If it is not here, the Tester
must ask rather than guess — and a guess that reaches the implementation is a
contamination event, not a shortcut.**

## Config seam (V1) — the surface the whole run existed to create

```python
kindex.config.bind_root(root: str | os.PathLike) -> None
kindex.config.unbind_root() -> None
kindex.config.active_root() -> Path | None
kindex.config.bound_root(root: str | os.PathLike) -> Iterator[Path]   # context manager
kindex.config.load_config(config_path=None, project_path=None, profile=None) -> Config
kindex.config.resolve_project_root(project_path: str | Path | None = None) -> Path
```

Binding semantics the oracle may rely on (Architecture Amendment 1):

- **Process-local.** A binding has no effect on a subprocess unless that
  subprocess binds for itself. *This one cost a full diagnosis round in v8*: a
  fixture created data through an in-process binding, then exercised the CLI as a
  child, which read an empty store and printed nothing. Give a child its own root
  by environment.
- **Takes effect after import**, for callers that already imported the module.
- **Reversible and idempotent.** `unbind_root()` with no binding is a no-op, never
  an error.
- **Not reentrant.** `bind_root` while already bound raises.
- **Total containment.** While bound, no resolution reads or writes any path
  outside the root — including config-file lookup, the data directory, scheduler
  logs, the degraded ledger, and any subprocess probe.

`Config` exposes `data_dir`, `data_path`, `scheduler_log_path`. `Store` instances
expose `db_path` (instance attribute, not a class attribute).

## MCP surface (V6)

```python
kindex.mcp_server._get_store() -> tuple[Store, Config]     # 2-tuple, NOT a bare Store
kindex.mcp_server.MemoryUnavailableError                    # subclasses RuntimeError
kindex.mcp_server._store, kindex.mcp_server._config         # module-level caches; set both to
                                                            # None to force re-resolution
```

The 2-tuple shape is Architecture Amendment 3, added mid-run precisely because it
was guessed wrong. A broken store must surface as the typed memory-unavailable
result at the tool boundary, never as a raised exception.

## Search and fence note (V3, V5)

```python
kindex.retrieve.hybrid_search(store, query, top_k=10, expand_graph=True,
                              graph_hops=1, ranking='ensemble', *,
                              include_expired=False, include_archived=False,
                              fence_stats: dict | None = None) -> list[dict]
kindex.retrieve.build_fence_note(results, fenced_nodes, top_k, include_archived) -> str
```

`fence_stats` is an **out-parameter the caller owns**: pass a dict, and on return
it holds `fenced` (int) and `fenced_nodes` (list of row dicts). Returns `""` when
`len(results) >= top_k`.

Disclosure channel is a property of the output MODE, never of the result count
(Product Spec Amendments 5 and 6): human-readable → stdout in every branch
including empty; `--json` → stderr always, because the stdout document must stay
parseable and its top-level shape is frozen by I5.

## Decay and ledger (V2, V4)

```python
Store.apply_weight_decay(node_half_life_days: int = 90,
                         edge_half_life_days: int = 30) -> int   # count of affected nodes
kindex.config.record_degraded(cmd, error, config=None, override_dir=None) -> None
kindex.config.degraded_ledger_path(config=None, override_dir=None) -> Path
```

`record_degraded` never raises into its caller (R4.2). Per-row decay accounting
lives in the existing `meta` key-value table under the `_wtr.` prefix — private,
**not** part of this contract, and an oracle must not assert on it. That
restriction is load-bearing: it is why one real invariant in this run is
documented as knowingly unguarded rather than covered by a test that reaches past
the seam.

## Test infrastructure

- `tests/conftest.py` is **read-only** to the Tester and registers no markers.
- The `red_now` marker is registered in the **rootdir** `conftest.py`, not under
  `tests/`. A `tests/conftest_batch0.py` would not be auto-loaded by pytest.
- The Tester authors new files under `tests/`; the rootdir conftest already
  exists in the projection and is tracked.

## Declared Tester projection

`tests`, `conftest.py`, `pyproject.toml`, `Makefile`, `README.md`, `CLAUDE.md`
(from `.harness/projection.conf`). Anything this document names must be reachable
from that set, or reachable only through a declared public import — never by
reading implementation source. `harness/projection_receipt.sh` enforces the path
half of this before dispatch.
