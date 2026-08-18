# TASK — Kindex state-resilience build

## User request

> Use the factory concept ~/Code/factory and ~/Code/tools on how we review and
> build production softare along with the Claude skills for /test and /engineer
> and /validate and /orchestrate; use Constrain and Sim and Advocate along with
> your own /ultraplan capability to devise a strategy for improving kindex and
> then implement that strategy.

Provider amendment:

> When building, prefer Ollama, Codex, and (for orchestrate, perhaps,
> antigravity [agy]) -- Claude is having issues.

Earlier state-management question in the same request chain:

> D'y'reckon that kindex could play a part in helping with this (or does it
> already?): Architecting Resilience: Diagnosing and Mitigating State Corruption
> in Long-Horizon AI Coding Agents

The attached report described bounded cognitive state, automatic capture
quarantine, explicit verification and erasure, contradiction/valid-time control,
hard resume bounds, checkpointing, and filesystem/runtime isolation. This run
implements the bounded Kindex-owned slice only; it does not claim host transcript
clearing, arbitrary time travel, runtime sandboxing, release, or deployment.

## Execution interpretation

- Factory artifacts freeze Product, Architecture, and Testing authority before
  code or acceptance tests.
- Independent Codex Coder and Tester lanes operate in separate worktrees.
- Root acts as Validator and may run tests, mutations, and bounded reviews but
  does not author Coder source or Tester acceptance tests.
- Ollama is the default model endpoint for bounded Advocate/Constrain work.
- Simulacrum is the explicitly requested narrow Anthropic exception.
- Antigravity is optional only; a failed or non-contained receipt is rejected.
- No merge to `main`, package publish, release, deployment, or live-graph
  mutation is authorized by this task.

Base revision: `666e20864fdcd3a21d5683f2c23085cf32d23257`.
