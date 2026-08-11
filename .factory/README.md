# .factory/ — shared factory-run record for this project

Per-project, git-tracked record of software-factory runs against this repo: signed
phase artifacts (spec / architecture / testing strategy), tool policy with amendments,
judge results, falsifiability evidence, incidents, and verdicts. The purpose is that
other teams and agents working on this project inherit the DECISIONS and their
evidence, not just the diffs. Workstation-private run state (schedule registries,
venvs, raw receipts, transcripts) stays in the local gitignored .harness/.
Graph-side, the same decisions live as kindex nodes tagged `batch0`/`factory`; the
run's node ids are cited inside these documents.
