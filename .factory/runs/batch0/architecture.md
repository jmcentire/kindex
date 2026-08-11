# Architecture Specification — run batch0

Base SHA 8c5cc925648c (origin/main incl. PR #18). Proposed by the Validator, ratified in
local AI-verdict mode (no human signature — flagged). Component ownership and criticality
below; the spec's requirement ids (R*.*) bind to these components.

## Component / state ownership

| Component | File(s) | Owns | Touched by |
|---|---|---|---|
| Hook command surface | `src/kindex/cli.py` (`main()`, `cmd_compact_hook`, status/doctor renderers) | argv dispatch, hook-vs-human distinction, degraded-output shapes | S1, S2 |
| Hook installer | `src/kindex/setup.py` (Claude hook entry management) | installed hook command lines; entry replacement/migration on re-run | S1 (R1.2) |
| Prime pipeline | `src/kindex/hooks.py` (`prime_context`) | section shields; partial-context composition | S2 (R2.4) |
| MCP boundary | `src/kindex/mcp_server.py` (store singleton getter) | store-open failure conversion to typed tool result | S2 (R2.5) |
| Degraded ledger | new small helper (location: wherever the base-data-dir anchor already lives — follow the issue-#15 scheduler-log pattern in `config.py`) | append + size-cap of `degraded.jsonl`; read side for status/doctor | S2 (R2.2, R2.3) |
| Search core | `src/kindex/store.py` (`fts_search`) | status fencing + `include_archived` param | S3 (R3.1) |
| Retrieval ensemble | `src/kindex/retrieve.py` (`hybrid_search`, context formatters) | backfill loop; operational-pull status filters | S3 (R3.2, R3.3) |
| Decay engine | `src/kindex/store.py` (`apply_weight_decay`) + meta table key | cadence-independent fold; cold-start stamp | S4 |

State notes:
- The degraded ledger is deliberately OUTSIDE SQLite (plain file in the base data dir)
  because its write path must survive a broken DB. It is run-state, not graph state.
- The decay checkpoint is ONE meta-table key (`decay.last_run` or equivalent); the meta
  table already exists — this is a row, not schema.
- Dependency direction unchanged: cli → hooks/store/retrieve; mcp_server → store;
  nothing new imports cli.

## Surface criticality (per-surface, explicitly decided)

Doctrine default is human-decided; the founder's blanket authorization for this run is in
TASK.md and the profile below is AI-proposed under it — founder review invited, and the
verdict will restate this. No surface in this run is classified Critical: every change is
to a single-user local tool, versioned in git, recoverable by revert/yank; nothing touches
money, regulated data, or an irreversible external effect.

| Surface | Class | Cost of being wrong | Declared side-effect edges |
|---|---|---|---|
| compact-hook envelope handling (S1) | Standard | capture stays broken or hook crashes at session end | setup.py entry migration; extraction/budget path (spend) |
| setup.py hook entry rewrite (R1.2) | Standard | user's installed hooks broken until re-run | Claude Code settings.json hook entries |
| main() catch-all for hook commands (S2) | Standard | masks real errors if over-broad (mitigated: hook-commands-only + ledger) | every installed hook |
| degraded.jsonl ledger (S2) | Standard | silent-failure regression if append itself throws | none (file append, best-effort) |
| fts_search fence + hybrid backfill (S3) | Standard | wrong recall set for every search | MCP search, CLI search, prime, context formatters |
| apply_weight_decay fold (S4) | Standard | graph-wide weight distortion (slow, visible, reversible — weights are advisory ranking state) | daemon cron cycle |
| status/doctor surfacing (S2) | Cosmetic (explicit decision) | a health line is wrong | none |
| Release 0.30.0 (S5) | Standard | broken public release — recoverable (yank / 0.30.1), gated by CI + fresh-install verify | PyPI, GitHub release, MCP registry metadata |

Unlisted surfaces: untouched by this run; any lane discovering it must touch an unlisted
surface reports a specification defect instead of proceeding (unclassified = Critical =
stop).

## Trust boundaries

- Hook stdin is UNTRUSTED input: the envelope parser must treat malformed JSON as
  not-an-envelope (fall back to --text/no-op), never crash, never eval.
- Transcript files are UNTRUSTED content: existing extraction hardening (#14) applies
  unchanged; this run only routes the correct bytes to it.
- The degraded ledger is append-only from the writer's perspective; the size-cap rewrite
  is the only mutation, and a failed cap rewrite must not lose the append.

## Deployment shape

Single wheel on PyPI; hooks installed into Claude Code settings by `kin setup`; cron /
launchd cadence from `kin setup-cron` (unchanged this run). Rollback posture: git revert +
patch release; PyPI yank for a broken artifact.
