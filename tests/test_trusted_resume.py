"""Acceptance oracles for verification, valid time, and trusted resume."""
from __future__ import annotations

import inspect
import json
import re

import pytest

from kindex.config import Config
from kindex.store import Store


AT = "2026-08-18T12:00:00Z"


@pytest.fixture
def store(tmp_path):
    value = Store(Config(data_dir=str(tmp_path)))
    yield value
    value.close()


def _verified(
    store: Store,
    title: str,
    *,
    node_id: str,
    status: str = "active",
    valid_at: str | None = None,
    invalid_at: str | None = None,
) -> str:
    created = store.add_node(
        title, content=f"content for {title}", node_id=node_id, status=status,
        prov_when="2025-01-01T00:00:00Z",
    )
    store.verify_node(
        created,
        verified_by="test reviewer",
        prov_method="fixture inspection",
        verified_at="2026-08-18T11:00:00Z",
        valid_at=valid_at,
        invalid_at=invalid_at,
    )
    return created


def _byte_count(text: str) -> int:
    return len(text.encode("utf-8"))


def _codepoint_count(text: str) -> int:
    return len(text)


def _word_count(text: str) -> int:
    return len(text.split())


@pytest.mark.red_now
def test_p4_1_p4_2_verify_and_invalidate_preserve_original_provenance(store):
    """P4.1/P4.2: a legacy node reaches verify then invalidate; rewritten
    prov_when/content/status or missing audit text is forbidden, while normalized
    typed fields and activity evidence are demanded. Updating prov_when instead
    of verified_at is the smallest reversion that turns red.
    """
    node_id = store.add_node(
        "Correctable fact", content="original content", node_id="correctable",
        prov_when="2024-05-01T10:00:00Z",
    )
    verified = store.verify_node(
        node_id,
        verified_by="  asserted reviewer  ",
        prov_method="  source comparison  ",
        verified_at="2026-08-18T07:00:00-05:00",
        valid_at="2026-08-01T00:00:00+00:00",
    )
    assert verified["prov_when"] == "2024-05-01T10:00:00Z"
    assert verified["verified_at"] == AT
    assert verified["verified_by"] == "asserted reviewer"
    assert verified["prov_method"] == "source comparison"
    assert verified["valid_at"] == "2026-08-01T00:00:00Z"
    assert verified["invalid_at"] is None

    invalidated = store.invalidate_node(
        node_id,
        invalidated_by="  correcting actor  ",
        disposition_code="  superseded_fact  ",
        invalid_at="2026-08-18T12:30:00+00:00",
    )
    assert invalidated["invalid_at"] == "2026-08-18T12:30:00Z"
    assert invalidated["prov_when"] == "2024-05-01T10:00:00Z"
    assert invalidated["content"] == "original content"
    assert invalidated["status"] == "active"
    activity = json.dumps(store.recent_activity(50))
    assert "asserted reviewer" in activity
    assert "source comparison" in activity
    assert "correcting actor" in activity
    assert "superseded_fact" in activity


@pytest.mark.red_now
def test_p4_3_rfc3339_normalization_and_invalid_inputs():
    """P4.3/A3: declared parser/normalizer functions receive offset, naive,
    leap-second, malformed-offset, and reversed intervals; acceptance of a
    forbidden timestamp is forbidden, while UTC normalization and ValueError
    are demanded. Replacing strict parsing with datetime.fromisoformat alone
    is the smallest mutation that turns red.
    """
    from kindex.trust import (
        normalize_rfc3339,
        parse_rfc3339,
        validate_interval,
    )

    parsed = parse_rfc3339("2026-08-18T07:00:00.123456-05:00", field="probe")
    assert normalize_rfc3339(parsed, field="probe") == (
        "2026-08-18T12:00:00.123456Z"
    )
    for bad in (
        "2026-08-18T12:00:00",
        "2026-08-18T11:59:60Z",
        "2026-08-18T12:00:00-0500",
        "not-a-time",
    ):
        with pytest.raises(ValueError):
            parse_rfc3339(bad, field="probe")
    for valid_at, invalid_at in (
        (AT, AT),
        ("2026-08-18T12:00:01Z", AT),
    ):
        with pytest.raises(ValueError):
            validate_interval(valid_at, invalid_at)


@pytest.mark.red_now
def test_p4_1_p4_2_audit_strings_are_bounded_nonempty_and_control_free(store):
    """P4.1/P4.2/A4: verify/invalidate receive blank, C0, and 129-character
    audit values; stored forbidden text is disallowed and ValueError is
    demanded. Removing control validation is the smallest mutation red.
    """
    node_id = store.add_node("Audit bounds", node_id="audit-bounds")
    verify_cases = [
        {"verified_by": " ", "prov_method": "method"},
        {"verified_by": "reviewer", "prov_method": "\x1bmethod"},
        {"verified_by": "r" * 129, "prov_method": "method"},
    ]
    for case in verify_cases:
        with pytest.raises(ValueError):
            store.verify_node(node_id, verified_at=AT, **case)
    for actor, code in ((" ", "code"), ("actor", "bad\x00code"),
                        ("actor", "x" * 129)):
        with pytest.raises(ValueError):
            store.invalidate_node(
                node_id, invalidated_by=actor, disposition_code=code,
                invalid_at=AT,
            )
    node = store.get_node(node_id)
    assert node["verified_at"] is None
    assert node["invalid_at"] is None


@pytest.mark.red_now
def test_p4_4_valid_and_invalid_boundaries_are_inclusive_then_exclusive(store):
    """P4.4: one verified interval is evaluated immediately before/at each
    boundary; early inclusion or invalid_at equality inclusion is forbidden,
    while exact not_yet_valid/trusted/invalidated reasons are demanded. Changing
    either <= comparison is the smallest mutation that turns red.
    """
    from kindex.trust import node_trust_decision

    node_id = _verified(
        store, "Boundary fact", node_id="boundary",
        valid_at="2026-08-18T12:00:00Z",
        invalid_at="2026-08-18T13:00:00Z",
    )
    node = store.get_node(node_id)
    assert node_trust_decision(
        store, node, at="2026-08-18T11:59:59.999999Z"
    ).reason == "not_yet_valid"
    assert node_trust_decision(store, node, at=AT).reason == "trusted"
    assert node_trust_decision(
        store, node, at="2026-08-18T12:59:59.999999Z"
    ).reason == "trusted"
    assert node_trust_decision(
        store, node, at="2026-08-18T13:00:00Z"
    ).reason == "invalidated"


@pytest.mark.red_now
def test_r1_5_r1_6_trust_reasons_are_distinct_and_resume_discloses_denials(store):
    """R1.5/R1.6: five linked node states reach the shared predicate and
    resume projection; admission of legacy/future/invalid/inactive values or a
    collapsed reason bucket is forbidden, while only the trusted title and
    every stable machine reason are demanded. Replacing reasoned filtering with
    status-only filtering is the smallest mutation that turns red.
    """
    from kindex.sessions import format_resume_context, link_node_to_tag, start_tag
    from kindex.trust import filter_trusted_nodes

    trusted = _verified(store, "Trusted recall", node_id="trusted")
    legacy = store.add_node("Legacy recall", node_id="legacy")
    future = _verified(
        store, "Future recall", node_id="future",
        valid_at="2026-08-18T12:00:01Z",
    )
    invalid = _verified(
        store, "Invalid recall", node_id="invalid", invalid_at=AT,
    )
    inactive = _verified(
        store, "Inactive recall", node_id="inactive", status="archived",
    )
    nodes = [store.get_node(item) for item in (
        trusted, legacy, future, invalid, inactive
    )]
    admitted, counts = filter_trusted_nodes(store, nodes, at=AT)
    assert [row["id"] for row in admitted] == [trusted]
    assert counts["unverified"] == 1
    assert counts["not_yet_valid"] == 1
    assert counts["invalidated"] == 1
    assert counts["inactive"] == 1

    start_tag(store, "trust-reasons", focus="reason projection")
    for node_id in (trusted, legacy, future, invalid, inactive):
        link_node_to_tag(store, "trust-reasons", node_id)
    output = format_resume_context(
        store, "trust-reasons", max_tokens=4096, evaluation_time=AT
    )
    assert "Trusted recall" in output
    for forbidden in (
        "Legacy recall", "Future recall", "Invalid recall", "Inactive recall"
    ):
        assert forbidden not in output
    lowered = output.lower()
    for reason in ("unverified", "not_yet_valid", "invalidated", "inactive"):
        assert reason in lowered


@pytest.mark.red_now
def test_r1_5_two_current_verified_contradictions_are_mutually_omitted(store):
    """R1.5/R1.6/P4.5: two linked verified nodes plus one explicit edge reach
    both directional decisions and resume; admitting either endpoint or counting
    only one is forbidden, while two mutual_contradiction denials are demanded.
    Ignoring incoming edges is the smallest mutation that turns red.
    """
    from kindex.sessions import format_resume_context, link_node_to_tag, start_tag
    from kindex.trust import node_trust_decision

    left = _verified(store, "Claim left", node_id="claim-left")
    right = _verified(store, "Claim right", node_id="claim-right")
    store.add_edge(left, right, edge_type="contradicts", provenance="explicit")
    assert node_trust_decision(store, store.get_node(left), at=AT).reason == (
        "mutual_contradiction"
    )
    assert node_trust_decision(store, store.get_node(right), at=AT).reason == (
        "mutual_contradiction"
    )
    start_tag(store, "contradiction", focus="explicit conflict")
    link_node_to_tag(store, "contradiction", left)
    link_node_to_tag(store, "contradiction", right)
    output = format_resume_context(
        store, "contradiction", max_tokens=4096, evaluation_time=AT
    )
    assert "Claim left" not in output
    assert "Claim right" not in output
    assert "mutual_contradiction" in output.lower()
    assert re.search(r"mutual_contradiction[^\n]*2|2[^\n]*mutual_contradiction",
                     output.lower())


@pytest.mark.red_now
def test_r1_7_untrusted_contradiction_endpoints_cannot_poison_verified_nodes(store):
    """R1.7/P4.5: unverified, future, invalidated, and inactive endpoints each
    explicitly contradict a separate verified subject; suppression of a subject
    is forbidden, while four trusted decisions are demanded. Removing the
    endpoint pre-eligibility gate is the smallest mutation that turns red.
    """
    from kindex.trust import node_trust_decision

    endpoint_ids = []
    endpoint_ids.append(store.add_node("Poison unverified", node_id="poison-u"))
    endpoint_ids.append(_verified(
        store, "Poison future", node_id="poison-f",
        valid_at="2026-08-18T12:00:01Z",
    ))
    endpoint_ids.append(_verified(
        store, "Poison invalid", node_id="poison-i", invalid_at=AT,
    ))
    endpoint_ids.append(_verified(
        store, "Poison inactive", node_id="poison-a", status="archived",
    ))
    for index, endpoint in enumerate(endpoint_ids):
        subject = _verified(
            store, f"Poison-resistant subject {index}", node_id=f"subject-{index}"
        )
        store.add_edge(
            subject, endpoint, edge_type="contradicts", provenance="poison probe"
        )
        decision = node_trust_decision(store, store.get_node(subject), at=AT)
        assert decision.eligible is True
        assert decision.reason == "trusted"


@pytest.mark.red_now
def test_p4_5_invalidation_resolves_explicit_conflict_without_semantic_inference(store):
    """P4.5: an explicit conflict is invalidated and a merely opposite-sounding
    pair has no edge; semantic winner inference is forbidden, while the surviving
    endpoint and both unlinked nodes are demanded trusted. Removing exclusive
    invalid-time evaluation is the smallest mutation that turns red.
    """
    from kindex.trust import node_trust_decision

    left = _verified(store, "Service is enabled", node_id="enabled")
    right = _verified(store, "Service is disabled", node_id="disabled")
    store.add_edge(left, right, edge_type="contradicts", provenance="explicit")
    assert not node_trust_decision(store, store.get_node(left), at=AT).eligible
    store.invalidate_node(
        right, invalidated_by="tester", disposition_code="superseded",
        invalid_at=AT,
    )
    assert node_trust_decision(store, store.get_node(left), at=AT).reason == "trusted"

    yes = _verified(store, "Deployment succeeded", node_id="semantic-yes")
    no = _verified(store, "Deployment failed", node_id="semantic-no")
    assert node_trust_decision(store, store.get_node(yes), at=AT).eligible
    assert node_trust_decision(store, store.get_node(no), at=AT).eligible


@pytest.mark.red_now
def test_r1_8_resume_identifies_context_as_data_not_authority(store):
    """R1.8: a real session reaches resume; instruction/intent authority claims
    are forbidden, while explicit context-as-data and not-authority language is
    demanded. Deleting the fixed warning is the smallest mutation that turns
    red.
    """
    from kindex.sessions import format_resume_context, start_tag

    start_tag(store, "authority-warning", focus="safe context")
    output = format_resume_context(
        store, "authority-warning", max_tokens=4096, evaluation_time=AT
    ).lower()
    assert "context" in output and "data" in output
    assert "not" in output and "authorit" in output


@pytest.mark.red_now
@pytest.mark.parametrize(
    "counter",
    [_byte_count, _codepoint_count, _word_count],
    ids=["utf8-bytes", "codepoints", "words"],
)
def test_r1_1_supplied_deterministic_counters_enforce_absolute_budget(store, counter):
    """R1.1/R1.2: a Unicode-heavy session reaches byte/codepoint/word counters
    at eight small budgets; any count above budget or nonempty nonpositive result
    is forbidden, while exact selected-unit bounds are demanded. Removing the
    final whole-output check is the smallest mutation that turns red.
    """
    from kindex.sessions import format_resume_context, start_tag

    start_tag(
        store, "counter-bounds", description="é漢🙂 " * 80,
        focus="focus é漢🙂 " * 40, remaining=["remain é漢🙂 " * 30],
    )
    assert format_resume_context(
        store, "counter-bounds", max_tokens=0, counter=counter,
        evaluation_time=AT,
    ) == ""
    assert format_resume_context(
        store, "counter-bounds", max_tokens=-7, counter=counter,
        evaluation_time=AT,
    ) == ""
    for budget in (1, 2, 5, 9, 16, 31, 64, 127):
        output = format_resume_context(
            store, "counter-bounds", max_tokens=budget, counter=counter,
            evaluation_time=AT,
        )
        assert counter(output) <= budget


@pytest.mark.red_now
def test_r1_2_default_budget_is_documented_and_measured_as_utf8_bytes(store):
    """R1.2/A7: the default counter processes multibyte resume data and its
    public docstring is inspected; provider-token/exact-token claims and byte
    overflow are forbidden, while documented Kindex byte units are demanded.
    Reverting the default to a character heuristic is the smallest mutation red.
    """
    from kindex.sessions import format_resume_context, start_tag

    start_tag(store, "byte-default", focus="漢🙂é" * 200)
    for budget in (17, 31, 63, 127, 255):
        output = format_resume_context(
            store, "byte-default", max_tokens=budget, evaluation_time=AT
        )
        assert len(output.encode("utf-8")) <= budget
    documentation = (inspect.getdoc(format_resume_context) or "").lower()
    assert "byte" in documentation
    assert not re.search(r"exact\s+(?:provider\s+)?tokens?", documentation)


@pytest.mark.red_now
def test_r1_3_r1_4_projection_is_deterministic_structural_and_priority_packed(store):
    """R1.3/R1.4: oversized control-bearing focus/history/knowledge reaches
    every projection tier and the first budget containing focus+remaining is
    selected; control bytes, broken labels/list prefixes, lower-priority leakage,
    or nondeterminism are forbidden, while structural deterministic output with
    current focus/remaining is demanded. Packing history before current state is
    the smallest mutation that turns red.
    """
    from kindex.sessions import (
        add_segment,
        format_resume_context,
        link_node_to_tag,
        start_tag,
    )

    start_tag(
        store, "priority-pack",
        description="DESCRIPTION OLD " * 80,
        focus="OLD FOCUS " * 80,
        remaining=["REMAINING CANARY", "remain two"],
    )
    add_segment(
        store, "priority-pack", new_focus="FOCUS CANARY 漢🙂\x1b[31m",
        summary="HISTORY CANARY " * 120,
    )
    related = _verified(
        store, "KNOWLEDGE CANARY " * 50, node_id="priority-knowledge"
    )
    link_node_to_tag(store, "priority-pack", related)
    full = format_resume_context(
        store, "priority-pack", max_tokens=20000, evaluation_time=AT
    )
    first_budget = next(
        budget
        for budget in range(1, len(full.encode("utf-8")) + 1)
        if "FOCUS CANARY" in format_resume_context(
            store, "priority-pack", max_tokens=budget, evaluation_time=AT
        )
        and "REMAINING CANARY" in format_resume_context(
            store, "priority-pack", max_tokens=budget, evaluation_time=AT
        )
    )
    output = format_resume_context(
        store, "priority-pack", max_tokens=first_budget, evaluation_time=AT
    )
    repeat = format_resume_context(
        store, "priority-pack", max_tokens=first_budget, evaluation_time=AT
    )
    assert output == repeat
    assert "FOCUS CANARY" in output and "REMAINING CANARY" in output
    assert "HISTORY CANARY" not in output
    assert "KNOWLEDGE CANARY" not in output
    assert "\ufffd" not in output
    output.encode("utf-8", errors="strict")
    assert all(
        character in "\n\t" or (ord(character) >= 32 and not 127 <= ord(character) <= 159)
        for character in output
    )
    for line in output.splitlines():
        assert not re.fullmatch(r"\s*[-*+]\s*", line)
        if line.lstrip().startswith("**"):
            assert line.count("**") >= 2


@pytest.mark.red_now
def test_r1_3_evaluation_time_changes_only_time_eligible_knowledge(store):
    """R1.3/R4.3: identical graph/counter/budget is evaluated before and at a
    future node's valid_at; ambient-clock drift and static-session changes are
    forbidden, while byte-identical repeats and only eligibility appearance are
    demanded. Calling now separately per node is the smallest mutation that
    turns red.
    """
    from kindex.sessions import format_resume_context, link_node_to_tag, start_tag

    future = _verified(
        store, "Time gated knowledge", node_id="time-gated",
        valid_at="2026-08-18T12:00:01Z",
    )
    start_tag(store, "time-projection", focus="STATIC FOCUS")
    link_node_to_tag(store, "time-projection", future)
    before = format_resume_context(
        store, "time-projection", max_tokens=4096,
        evaluation_time="2026-08-18T12:00:00Z",
    )
    before_again = format_resume_context(
        store, "time-projection", max_tokens=4096,
        evaluation_time="2026-08-18T12:00:00Z",
    )
    at_boundary = format_resume_context(
        store, "time-projection", max_tokens=4096,
        evaluation_time="2026-08-18T12:00:01Z",
    )
    assert before == before_again
    assert "STATIC FOCUS" in before and "STATIC FOCUS" in at_boundary
    assert "Time gated knowledge" not in before
    assert "Time gated knowledge" in at_boundary
