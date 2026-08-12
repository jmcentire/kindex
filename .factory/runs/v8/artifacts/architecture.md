# Architecture Specification — run v8

Base SHA `d4ecf5cfed00` (main, post-0.30.1 plus the batch0 record). Proposed by the
Validator, ratified in local AI-verdict mode (no human signature — flagged).

## Component / state ownership

| Component | File(s) | Owns | Requirements |
|---|---|---|---|
| Config resolution | `src/kindex/config.py` | the resolution order, the module-level cache, and the new test seam | V1 |
| MCP store accessor | `src/kindex/mcp_server.py` | honoring the seam at the surface whose absence caused the descope | R1.2, R6.1 |
| Decay engine | `src/kindex/store.py` (`apply_weight_decay`) + `decay.*` meta rows | schedule-independent folding, cold start, anti-starvation | V2 |
| Degraded ledger | `src/kindex/config.py` (`record_degraded`) | append/cap atomicity, never raising | V4 |
| Retrieval | `src/kindex/retrieve.py` (`hybrid_search`) | backfill bound or its documentation | V5 |
| Search surfaces | `src/kindex/cli.py`, `src/kindex/mcp_server.py` | fence-note honesty | V3 |

## Design constraints the implementer must respect

**V1 seam shape (constraint, not prescription).** The seam must satisfy R1.1–R1.5.
The obvious candidates are an explicit override function plus a context manager, or a
resolution hook consulted before the cache. **Whatever is chosen, the cache must be
part of the seam** — a seam that sets a variable but leaves a populated module cache
in front of it does not satisfy R1.2, and that failure mode is exactly what descoped
the batch0 test. Environment variables alone are insufficient by R1.1 (they cannot
take effect post-import).

**V2 anti-staircase (the crux).** Batch0's gate skipped the *fold* when the interval
was short, which is what created the phase dependence. R2.1 and R2.4 must hold
together. The known-good shape is to make the fold unconditional and move any
threshold to the **write** decision, with per-row accounting so a suppressed write
does not discard the interval. Any design satisfying both requirements is acceptable;
a design that gates the fold is not.

**V4 atomicity.** A cap rewrite that reads, truncates, and replaces cannot be made
lossless by ordering alone. Either serialize the two operations (advisory lock on the
ledger path) or make the cap non-destructive (rotate rather than rewrite). R4.2 binds
absolutely: whatever is chosen must swallow its own errors.

## Surface criticality

All Standard. Nothing here touches money, regulated data, or an irreversible external
effect; everything is local, versioned, and revertible.

**Except one, elevated deliberately: R1.5 is CRITICAL.** A seam that leaks resolution
outside its binding causes a test to read or write a user's real knowledge graph. That
is data-integrity on someone's personal corpus, it already happened once, and it is
the reason this run exists. A gap on R1.5 **blocks** — no risk acceptance.

## Trust boundaries

- Test-supplied roots are untrusted paths: the seam must not follow them outside
  itself, and must not create directories outside the binding.
- The decay checkpoint is machine-local state; clock skew (a future `last_accessed`)
  must not raise and must not boost a weight.

## Deployment

Single wheel; no migration; rollback is revert plus a patch release. The `[all]` and
`[mcp]` extras stay pinned below mcp 2.0 and the isolation gate keeps exercising the
`[all]` install path.

---

## AMENDMENT 1 — the V1 seam interface contract (binding on both lanes)

Raised by the Tester as `DEFECT->VALIDATOR`, correctly: the specification stated the
seam's *requirements* but left its *shape* to the implementer, which is impossible
for a party that cannot see the implementation. Settling an interface contract that
two non-communicating lanes must build and test against is Phase A work, and its
absence was a Phase A defect. This amendment is authoritative; it supersedes the
"constraint, not prescription" latitude for V1 only.

### Contract — `src/kindex/config.py`

```python
def bind_root(root: str | os.PathLike) -> None
def unbind_root() -> None
def active_root() -> pathlib.Path | None
@contextlib.contextmanager
def bound_root(root: str | os.PathLike) -> Iterator[pathlib.Path]
```

Semantics, all binding:

1. **Effect.** While a binding is active, every config resolution — data dir, config
   file, and any derived path — resolves beneath `root`. `HOME`, the environment, and
   the user config file are not consulted for path resolution while bound (R1.1).
2. **Post-import.** `bind_root` takes effect for callers that imported the module
   earlier: it MUST invalidate any module-level cache on both bind and release
   (R1.1, R1.2). A binding that leaves a populated cache in front of it does not
   satisfy this contract.
3. **Universality.** Every surface resolving config honors it, explicitly including
   `kindex.mcp_server`'s store accessor (R1.2).
4. **Reversibility.** `unbind_root` restores normal precedence exactly (R1.4).
   `bound_root` binds on entry and releases on exit **even if the body raises**, and
   restores any previously active binding rather than clearing to none.
5. **Introspection.** `active_root()` returns the active binding or `None` — this is
   what a test asserts against, and what makes R1.5 checkable.
6. **Containment (CRITICAL, R1.5).** While bound, no path outside `root` is read,
   written, or created. Directories are created under `root` on demand only.
7. **Scope.** Process-local, not thread-local; not persisted; no effect on a
   subprocess unless that subprocess binds for itself.
8. **Unbound behavior is byte-identical to today** (R1.3). No new precedence tier.

Errors: `bind_root` on an already-bound process raises `RuntimeError` naming the
active root (nested binding is a test bug, not a feature); `unbind_root` with no
binding is a no-op, never an error.

---

## AMENDMENT 3 — the MCP store accessor's shape, declared for the oracle

The R1.2 test failed with `'tuple' object has no attribute 'db_path'`. That is not an
implementation defect: `_get_store()` returns `(Store, Config)` and has done since
before this run. The Tester could not know that — Amendment 1 declared the seam's API
but never the accessor R1.2 requires it to verify, so the Tester guessed once at a
surface it was forbidden to read. Same root cause as the defect it correctly refused
to guess about earlier; this time there was nothing to signal that a guess was being
made.

Declared, for oracle use only:

- `kindex.mcp_server._get_store()` returns the 2-tuple `(store, config)`.
- `store.db_path` is the resolved database path; `config` is the resolved `Config`.
- Neither is part of the public API. A test asserts against them to prove R1.2's
  binding is honored; nothing else may depend on this shape, and the implementation
  is NOT to be reshaped to match a test's expectation.

Consequence for judgment: the three R1.4 failures and probably several others are a
CASCADE from this one crash — R1.2 bound a root, raised before releasing, and the
next test reported "a prior test leaked its config binding", with a later failure
naming R1.2's own temp directory as the still-active root. The oracle needs teardown
that survives a failing test; until it has that, one broken assertion contaminates
every test after it and the failure count is not evidence about the implementation.
