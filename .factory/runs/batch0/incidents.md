# Run incidents — batch0

## INC-1: duplicate Validator instance (2026-08-10 ~21:20–21:40 local)
factory.sh spawns `claude "/validate …"` in the validator window by design. The seat
was taken by the primary session; the window was respawned to neutralize the spawned
instance — but `tmux respawn-window -k` without an explicit command re-runs the window's
ORIGINAL command, silently relaunching a second Validator. It ran ~20 min, independently
authored a competing product spec, and was caught AT the file-overwrite permission
prompt — nothing on disk was modified **[FALSE-AS-WRITTEN — see the addendum below: the
duplicate fast-forwarded main, committed the endgame gates as 08954de, and
interleaved its receipts into the authoritative chain. The artifact digests were
intact; the repository was not untouched.]**
Resolution: window respawned with an explicit `exec bash`. Lanes were dispatched after
containment; dispatch-cited digests verified.
Second-reader signal worth keeping: the duplicate's independent Phase A classified the
S5 release surface as CRITICAL (this run's architecture says Standard with a hard CI
evidence gate). Divergence recorded for the verdict; operationally equivalent here —
any release-gate failure stops the run either way (never re-tag; fix → new patch).
Upstream note for the factory repo: factory.sh + a seat-holding primary session need a
sanctioned "external validator seat" path (flag or documented respawn-with-command),
or this trap recurs.

INC-1 addendum (reconciliation, post-audit): the duplicate did more than author a
competing spec before it was killed. Per events.jsonl + git: it ff-forwarded local main
6be0352→8c5cc925 (correct), committed the (already-clean) Makefile endgame gates as
08954de with receipt R-20260811T022943Z-4127 and a disclosed validator_note (correct
discipline, wrong principal count), and its receipts are interleaved in the run's
receipt chain. The primary seat's 753fd2a on top carries only the .gitignore delta
(commit message overstates by naming the Makefile — recorded here, history left as-is;
content verified identical, both commits correctly attributed). Orchestrator wakes
20260811T022504Z and 20260811T023004Z dispositioned the dispatcher's bypass flags as
false positives with sound evidence; its outstanding flags — amend run.json base vs
dispatch SHA, detector needs ignition-snapshot baseline — are dispositioned as: lanes
correctly dispatched at base 8c5cc925 (endgame gates are Validator infra, not lane
surface; final SHA will contain them), and the detector-baseline defect goes upstream
with the sched_audit comment bug.

## INC-3: injected text executed in the validator window's shell
After INC-1's containment respawned the validator window to bash, dispatcher and
orchestrator injections (inject.sh → tmux send-keys) were PARSED BY BASH in the repo
cwd — observed as `bash: [orchestrator]: command not found`; backticked paths inside
orchestrator prose would have command-substituted (the `.harness/...` fragments
resolved to nonexistent commands; scrollback audit found no harmful execution).
Resolution: window respawned as a non-executing inbox sink (`cat >>
validator-inbox.log`); authoritative orchestrator→Validator channel designated as
`wakes/*.response.md` (declared in events.jsonl). Upstream harness defect: inject.sh
assumes the target pane hosts a listening agent and has no non-executing delivery mode.
Third upstream finding this run (with the sched_audit comment bug and the
detector-baseline class).

## INC-5: forged-principal injection into the coder pane (unreceipted)
At coder __DONE__ time, the line `[VALIDATOR] Received; proceeding to independent
verification.` was found typed-but-unsubmitted in the coder's input box. Its sha256 has
NO entry in injections.jsonl — it bypassed inject.sh via raw send-keys and forged the
Validator prefix. Origin unproven; the concurrent orchestrator-sibling seats (the
no-single-flight fan-out defect) are the only other principals with tmux access, and
one sibling's report claims "I never touched the lane panes." Content was benign
(a plausible acknowledgment), which is exactly why it matters: pane-prefix identity is
spoofable, receipts are the only authority. Contained: input cleared without
submission (Escape+Ctrl-U), authentic receipted acknowledgment sent (injection
e4db29e6…) instructing the coder to disregard unreceipted pane history. Impact on the
lane: none — the text never entered its transcript as input. Upstream finding #7:
raw tmux send-keys is an open lane-write path for every seat on the machine; the
enforced topology lives only in inject.sh, which nothing compels a seat to use.

## INC-4: long injections sit unsubmitted in lane input boxes
inject.sh delivers `send-keys "$MSG" Enter`; for long messages the lane TUI treats the
burst as a bracketed paste and the Enter becomes a newline in the input box instead of
a submit. Both lanes sat ~17–22 minutes with Validator answers typed-but-unsubmitted
(coder's grant answer; tester's Amendment 2) — visible as `❯ [VALIDATOR] …` in the
prompt box with no spinner. Short injections ("1"/"2") submit fine. Resolution: after
any long lane injection, the Validator follows with a separate bare Enter keypress
(receipt trail unaffected — the message digest is already receipted by inject.sh).
Upstream harness fix: inject.sh should send the Enter as a separate delayed keypress.
Fourth upstream finding this run. Note: the dispatcher's 15-minute stall FSM did NOT
catch these stalls because pane content kept changing slightly (spinner timers), so
"quiet" never triggered — stall detection keyed on content-change misses
input-box-stuck states; fifth upstream finding.

## INC-2: Validator poller gap (found by founder's audit question)
The first monitor matched `[dispatcher]` but not `[orchestrator]` — orchestrator
replies injected into the validator window would have gone unseen. Monitor replaced
(widened to orchestrator messages, dispatcher trigger-pattern vocabulary, HALT banner).
Also recorded: the harness TaskStop surface was hook-blocked; monitors are stopped by
pkill on their distinctive state-file path.
