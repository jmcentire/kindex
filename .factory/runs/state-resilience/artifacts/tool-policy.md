# Tool Policy — state-resilience run

Authority state: local AI-ratified from the user request and provider amendment;
no human signature was supplied.

Base revision: `666e20864fdcd3a21d5683f2c23085cf32d23257`.

## Allowed without further authority

- Read the Kindex repository, frozen artifacts, build metadata, and role-allowed
  files in an owned worktree.
- Edit role-owned files with `apply_patch`.
- Run local formatting, static checks, build commands, and Validator-owned tests
  against temporary stores and stripped provider environments.
- Use non-destructive Git inspection, commits on owned branches, and isolated
  worktrees.
- Use Kindex MCP for task/session coordination and durable discoveries, while
  keeping test execution away from the live graph.
- Run bounded Constrain, Advocate, and Simulacrum reviews over explicit artifact
  or patch text. Ollama is preferred for Constrain/Advocate.

## Role restrictions

- Tester may inspect frozen artifacts, repository documentation/build metadata,
  and `tests/**`; it may not inspect `src/kindex/**`, Coder state, implementation
  diffs, or judge output, and it does not execute the judging suite.
- Coder may inspect frozen artifacts, repository source/documentation/build
  metadata; it may not inspect `tests/**`, Tester state, or Validator traces, and
  it does not issue a pass/fail or production-readiness verdict.
- Validator may inspect and execute integrated work, but does not author the
  Coder implementation or Tester acceptance oracles. It reports only bare
  requirement-level failures back to Coder.

## Requires new explicit authority

- Merge or push to `main` or another shared branch.
- Open/merge a pull request, tag, package, publish, release, deploy, or modify a
  hosted/live environment.
- Add authentication, compliance controls, encryption, a network service, or
  another materially broader architecture than the frozen artifacts.

## Verboten for this run

- Edit the shared primary checkout.
- Use Claude as the build/orchestration host. Simulacrum remains the sole narrow
  exception because the user explicitly required it.
- Let automatic capture call node/edge persistence or candidate acceptance.
- Use live Kindex graph or home-directory state in tests.
- Let tests make network/provider calls or consume production credentials.
- Give Coder acceptance-test contents, names, assertions, or failure traces.
- Give Tester source contents, Coder output, or judge results.
- Treat an agent's self-review, local green subset, optional Antigravity receipt,
  or unsigned artifact as human approval, release evidence, or deployment proof.
