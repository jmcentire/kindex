"""Evidence order and labels in kin ask's context (answer.assemble): rank_order,
position_labels, fact_sources; and queueing many nodes for embedding at once."""

from kindex import answer
from kindex.config import AskConfig, Config
from kindex.store import Store


def _chunk(nid, when, position, text):
    return {"id": nid, "type": "document", "title": "", "content": text, "prov_when": when,
            "prov_activity": "conversation-ingest", "prov_source": "c1_p2",
            "extra": {"conversation_id": "c1_p2", "position": position}}


def _claim(nid, text, position, excerpt):
    return {"id": nid, "type": "document", "title": "", "content": text, "prov_when": "2024-03-15",
            "prov_activity": "conversation-digest",
            "extra": {"conversation_id": "c1_p2", "kind": "conversation-fact", "fact_date": "2024-03-15",
                      "turn_claim": True, "source": {"position": position, "role": "user", "excerpt": excerpt}}}


RESULTS = [_chunk("late", "2024-03-15", 7, "user: the rate is now 12%"),
           _chunk("early", "2024-03-15", 2, "user: the rate is 15%"),
           _claim("f", "The estate-tax estimate is now 12%.", 7, "the rate is now 12%")]


def test_default_order_is_by_date_with_facts_first():
    text = answer.assemble(RESULTS, [], 4000).text
    assert text.index("Facts recorded") < text.index("Evidence, oldest first")
    assert "s2 #7" not in text and "said:" not in text


def test_rank_order_keeps_the_search_order_with_labels_and_sources():
    text = answer.assemble(RESULTS, [], 4000, rank_order=True, position_labels=True, fact_sources=True).text
    assert "most relevant first" in text
    late, early, fact = (text.index(s) for s in ("now 12%\n", "is 15%", "The estate-tax"))
    assert late < early < fact
    assert "[2024-03-15 · s2 #7]" in text and "[2024-03-15 · s2 #2]" in text
    assert "[s2 #7; the user said: “the rate is now 12%”]" in text


def test_position_order_sorts_a_days_excerpts_by_place_in_the_conversation():
    text = answer.assemble(RESULTS[:2], [], 4000, source_order=True).text
    assert text.index("is 15%") < text.index("now 12%")


def test_enqueue_embeddings_writes_the_queue_once(tmp_path, monkeypatch):
    from kindex import vectors

    store = Store(Config(data_dir=str(tmp_path)))
    try:
        ids = [store.add_node(f"n{i}", "text", queue_embedding=False) for i in range(5)]
        assert vectors._load_embedding_queue(store) == []
        writes = []
        real = vectors._write_embedding_meta
        monkeypatch.setattr(vectors, "_write_embedding_meta",
                            lambda *a, **kw: (writes.append(1), real(*a, **kw))[1])
        assert vectors.enqueue_embeddings(store, ids + ids[:2]) == 5
        assert writes == [1] and vectors._load_embedding_queue(store) == ids
    finally:
        store.close()


def test_hints_reach_the_answer_prompt_only_for_that_question():
    token = answer._HINTS.set(("List these topics in order: A, B.",))
    try:
        prompt = answer.answer_prompt("What came first?", "ctx", "ordering")
    finally:
        answer._HINTS.reset(token)
    assert answer.HINTS_HEADER in prompt and "- List these topics in order: A, B." in prompt
    assert prompt.index("What came first?") < prompt.index(answer.HINTS_HEADER)
    assert answer.HINTS_HEADER not in answer.answer_prompt("What came first?", "ctx", "ordering")


def test_leading_hints_replace_the_answering_rules():
    options = AskConfig(hint_mode="lead")
    token = answer._HINTS.set(("Report the value as first stated.",))
    try:
        system = answer.answer_system("fact", True, options=options)
        prompt = answer.answer_prompt("What did I say?", "ctx", "fact", options=options)
    finally:
        answer._HINTS.reset(token)
    assert system == answer.LEAD_SYSTEM and "most recent statement" not in system
    assert prompt.endswith(f"{answer.LEAD_HEADER}\n- Report the value as first stated.")
    assert answer.DEFAULT_STYLE not in prompt
    # Without hints, or with hints added, the rules stay.
    assert answer.answer_system("fact", True, options=options) != answer.LEAD_SYSTEM
    assert answer.answer_system("fact", True, options=AskConfig()) != answer.LEAD_SYSTEM


def test_leading_first_puts_the_task_first_and_the_question_last():
    options = AskConfig(hint_mode="lead_first")
    token = answer._HINTS.set(("Cover these aspects: A.",))
    try:
        prompt = answer.answer_prompt("Summarize my project.", "EVIDENCE", "summary", options=options)
    finally:
        answer._HINTS.reset(token)
    assert prompt.index(answer.LEAD_HEADER) < prompt.index("EVIDENCE")
    assert prompt.endswith(f"{answer.LEAD_AGAIN}Summarize my project.")


def test_leading_both_puts_the_task_around_the_evidence():
    options = AskConfig(hint_mode="lead_both")
    token = answer._HINTS.set(("Say there is no record if it is absent.",))
    try:
        prompt = answer.answer_prompt("What did I pay?", "EVIDENCE", "fact", options=options)
    finally:
        answer._HINTS.reset(token)
    first, second = (i for i in range(len(prompt)) if prompt.startswith(answer.LEAD_HEADER, i))
    assert first < prompt.index("EVIDENCE") < second
    assert prompt.endswith(f"{answer.LEAD_AGAIN}What did I pay?")
