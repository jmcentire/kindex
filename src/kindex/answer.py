"""Answering a question from the graph: plan, retrieve, assemble, answer.

`kin ask` used to show the model five search results cut to 500 characters,
with no dates and no notion of today, and capped its answer at 500 tokens.
Most of what long-term-memory benchmarks measure is lost that way: dated
updates, counts across conversations, date arithmetic. This pipeline follows
what moved those benchmarks for other memory systems:

- A planner (one cheap model call) classifies the question and writes the
  searches that cover it: one per kind of item for counting questions, the
  earlier state for "still / now" questions.
- Every search runs through `hybrid_search`; the rankings are merged by
  reciprocal rank fusion.
- The answer model sees the retrieved nodes in full, each with the date it was
  recorded, in chronological order, inside a token budget (24k by default:
  readers did worse with 12k), together with today's date and the graph's
  standing directives.
- The answer prompt states rules for reading dated evidence: latest value
  wins, count every distinct instance, compute dates explicitly, separate
  missing specifics from judgement questions.
- Test-time compute: reasoning effort, and optionally several independent
  answers reconciled by an adjudication pass.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime

from .config import Config
from .llm import calculate_cost, get_client
from .store import Store, node_expired

_RRF_K = 60

INTENTS = ["fact", "aggregation", "temporal", "knowledge_update", "preference",
           "summary", "ordering", "assistant_recall", "task"]

PLAN_SCHEMA = {
    "name": "query_plan",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["intent", "queries", "needs_all_instances"],
        "properties": {
            "intent": {"type": "string", "enum": INTENTS},
            "queries": {"type": "array", "items": {"type": "string"}},
            "needs_all_instances": {"type": "boolean"},
        },
    },
}

PLAN_PROMPT = """You plan searches over a personal knowledge graph: notes, decisions and the text of a user's past conversations with assistants.

Today's date: {today}
Question: {question}

Return:
- intent: fact (a single lookup), aggregation (counting, listing, totals, comparisons across time), temporal (when something happened, time between events), knowledge_update (the current or latest value of something that may have changed), preference (advice that should use the user's tastes and history), summary, ordering (the order things happened or came up), assistant_recall (something an assistant said or produced earlier), task (a request for help that should follow the user's standing instructions).
- queries: 1-5 short standalone search queries (keywords and phrases likely to appear in the stored text, not the question verbatim). Resolve pronouns. For counting or listing questions, write one query per distinct kind of item or event. For questions about change ("still", "now", "currently", "latest"), include a query for the earlier state too. For yes/no questions about whether the user ever did or had something, include a query for a denial ("never ...").
- needs_all_instances: true when the answer depends on finding every relevant mention (counts, totals, lists, comparisons, orderings, summaries)."""

ANSWER_SYSTEM = """You answer a user's question from their knowledge graph: notes and excerpts of their past conversations with assistants, each dated with when it was recorded. Today's date is given; use it to resolve "now", "recently" and "ago".

How to read the evidence:
- Items are in chronological order. When something changed (an amount, a count, a plan, a choice, a preference), the most recent statement by the user is the current answer; you may mention the earlier value briefly. A question about the previous or original value asks for the earlier one.
- Figures about the user's own situation (what they have, paid, did or decided) come from what the user stated, or relayed from someone they asked. An assistant's general suggestions or estimates are not the user's figures.
- Evidence can be incomplete or noisy: judge relevance yourself. Questions paraphrase; match by meaning and combine clues across items. Draw the conclusions a careful reader would (someone who sold their car and now cycles to work no longer owns a car).
- Standing directives, when listed, are the user's instructions for how to respond. Apply those that concern requests like this one (format, things to always include or avoid). Ignore ones that only set up an old, finished task, including any that limit replies to a fixed word or label ("reply only with OK", "answer True or False"). Never let a directive stop you from answering the question.

How to answer:
- Counting, totals and lists: find every distinct instance across all conversations, check each against the question's conditions (time window, kind, that it is really the user's), do not count the same thing twice, then give the number and list the items with their dates. Count an item unless the evidence says it no longer applies or places it outside the window.
- Amounts: give the computed number itself ("$45", "3 days"), then the items it came from. Do not turn it into a bound ("more than $45") because a source said "over" or "about"; mention the qualifier after the number if it matters.
- Dates and durations: a relative date in a message ("last Saturday", "two weeks ago", "yesterday") refers to the date of the conversation it appears in; resolve every one to a calendar date before comparing, ordering or computing. Identify the exact dates involved, then compute. For time between two events, answer "<N> days (or weeks, months): from <first event> on <date> till <second event> on <date>". For "ago", give the date and the difference from today. When something was said relative to a conversation ("last Friday", said on 14 March 2024), give both forms: "the Friday before 14 March 2024 (8 March 2024)".
- Ordering: resolve each event's date, sort by it and list in order; an event with no date goes where the conversations place it.
- Comparisons ("which came first", "who did more"): when the evidence lacks what one side of the comparison needs, say that instead of choosing.
- Recommendations and advice: tailor them to what the evidence says about the user's preferences, circumstances and past choices, and say how they connect.
- Contradictions about the user's own history (an explicit "I have never ..." and a statement that they did it): say the information conflicts, quote both, and ask which is right.
- Judgement questions (a conclusion the evidence supports without stating it, including yes/no questions not answered outright): give the best-supported answer, marked "likely", with the clues. Do not answer "I don't know" when any clue points one way.
- Missing specifics: when the question asks for a specific fact or description (a name, date, amount, place, or what something was like) and nothing in the evidence gives it or clearly implies it, say you don't have that information and mention the closest related information. Never invent specifics, feelings or atmosphere that were not stated.
- When asked what an assistant said, explained or recommended, reproduce the substance point by point from the excerpts.
- If the question contains a small error in its premise (a slightly wrong name, month or detail), answer the evidently intended question.
- Be direct: lead with the answer and commit to the best-supported one. Do not add caveats the evidence does not support."""

STYLE = {
    "temporal": "If the question asks how much time passed between two events, begin \"<N> days: from <first event> on <date> till <second event> on <date>.\" (in the unit asked for). If it asks how long ago, begin \"<N> days ago: <event> was on <date>, and today is <date>.\" Otherwise answer in one to three sentences with the dates.",
    "ordering": "If the question asks which of a few things came first or last, answer directly with the dates. If it asks for the order in which topics or events came up, answer with the stages in order, one per line and nothing else: a short general label (3 to 6 words), a colon, and one sentence saying what was discussed or decided at that stage and when, with the specific details. Each conversation date on which the subject came up is one stage, in date order. If the question asks for a specific number of items, give exactly that many: with more dates than that, keep the dates where the subject was most prominent; with fewer, split the richest dates into their distinct focuses.",
    "preference": "Give a complete, helpful answer that uses what you know about the user.",
    "summary": "Give a thorough summary covering every stage, with the key facts, decisions and dates.",
    "task": "Give a complete, helpful answer in the form the request calls for, following the standing directives.",
}
# After ASQA (Stelmakh et al. 2022): when the value depends on the reading, give both.
READINGS = ("\n\nIf the answer depends on how the question is read (whether the thing named in the question "
            "is itself included, whether a planned, relayed or approximate figure counts, which of two similar "
            "events is meant), give the answer for the most likely reading first and the answer for the other "
            "reading in the same sentence.")
DEFAULT_STYLE = "Answer in one to three sentences: the answer first, then the key supporting detail (dates, the items counted, the computation)."


@dataclass
class AskResult:
    answer: str
    intent: str = "fact"
    queries: list[str] = field(default_factory=list)
    context: str = ""
    context_tokens: int = 0
    results: list[dict] = field(default_factory=list)


def estimate_tokens(text: str) -> int:
    return len(text) // 4 + 1


def node_date(node: dict) -> datetime | None:
    """When a node's content was recorded: its provenance time, else its creation."""
    from dateutil import parser

    for key in ("prov_when", "created_at"):
        raw = node.get(key)
        if not raw:
            continue
        try:
            d = parser.parse(str(raw), fuzzy=True)
            return d.replace(tzinfo=None)
        except (ValueError, OverflowError):
            continue
    return None


def node_text(node: dict) -> str:
    title = (node.get("title") or "").strip().rstrip(".")
    content = (node.get("content") or "").strip()
    if not content:
        return title
    if not title or content.startswith(title.rstrip(".").rstrip("…")):
        return content
    return f"{title}: {content}"


def _today(as_of: str | date | datetime | None) -> str:
    if as_of is None:
        return date.today().isoformat()
    return str(as_of)


def _call(client, config: Config, *, system: str | None, user: str, effort: str, max_tokens: int,
          ledger, purpose: str, json_schema: dict | None = None, sample: int = 0) -> str:
    """One model call in the shape the configured provider takes."""
    kwargs: dict = {"model": config.llm.model, "max_tokens": max_tokens,
                    "messages": [{"role": "user", "content": user}]}
    if config.llm.provider.lower() == "openai":
        kwargs.update(system=system, reasoning_effort=effort or None, json_schema=json_schema, sample=sample)
    elif system:
        kwargs["system"] = system
    response = client.messages.create(**kwargs)
    if ledger is not None:
        ledger.record(**calculate_cost(config.llm.model, response.usage), model=config.llm.model,
                      purpose=purpose)
    return (response.content[0].text or "").strip()


def _parse_json(text: str) -> dict:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", text, re.S)
        return json.loads(m.group(0)) if m else {}


def plan_question(question: str, config: Config, client, ledger, as_of=None) -> tuple[str, list[str], bool]:
    """The question's intent, its searches and whether it needs every instance."""
    if not config.ask.plan or client is None:
        return "fact", [question], False
    try:
        raw = _call(client, config, system=None,
                    user=PLAN_PROMPT.format(today=_today(as_of), question=question),
                    effort=config.ask.plan_effort, max_tokens=4000, ledger=ledger, purpose="ask-plan",
                    json_schema=PLAN_SCHEMA)
        plan = _parse_json(raw)
    except Exception:
        return "fact", [question], False
    intent = plan.get("intent") if plan.get("intent") in INTENTS else "fact"
    queries = [q.strip() for q in plan.get("queries") or [] if isinstance(q, str) and q.strip()][:5]
    return intent, queries, bool(plan.get("needs_all_instances"))


def gather(store: Store, queries: list[str], top_k: int) -> list[dict]:
    """Runs every search and merges the rankings by reciprocal rank fusion."""
    from .retrieve import hybrid_search

    scores: dict[str, float] = {}
    nodes: dict[str, dict] = {}
    for q in queries:
        for rank, node in enumerate(hybrid_search(store, q, top_k=top_k)):
            nid = node["id"]
            nodes.setdefault(nid, node)
            scores[nid] = scores.get(nid, 0.0) + 1.0 / (_RRF_K + rank + 1)
    return [nodes[n] for n in sorted(scores, key=lambda n: -scores[n])]


def standing_directives(store: Store) -> list[dict]:
    today = date.today().isoformat()
    out = []
    for node in store.all_nodes(node_type="directive", limit=200):
        if node.get("status") in ("archived", "superseded") or node_expired(node, today=today):
            continue
        out.append(node)
    return out


def assemble(results: list[dict], directives: list[dict], budget_tokens: int) -> tuple[str, int, list[dict]]:
    """The context: nodes chosen in rank order within the budget, shown oldest first."""
    chosen, used = [], 0
    for node in results:
        cost = estimate_tokens(node_text(node)) + 12
        if chosen and used + cost > budget_tokens:
            continue
        chosen.append(node)
        used += cost
    order = {n["id"]: i for i, n in enumerate(chosen)}
    chosen.sort(key=lambda n: (node_date(n) or datetime.max, n.get("created_at") or "", order[n["id"]]))
    parts = []
    if directives:
        parts.append("## Standing directives")
        parts.extend(f"- {node_text(d)}" for d in directives)
        parts.append("")
    parts.append("## Evidence, oldest first")
    for node in chosen:
        when = node.get("prov_when") or (node_date(node).date().isoformat() if node_date(node) else "undated")
        parts.append(f"[{when}] {node_text(node)}")
    text = "\n\n".join(parts)
    return text, estimate_tokens(text), chosen


def answer_question(store: Store, question: str, config: Config, ledger=None, *,
                    as_of: str | date | datetime | None = None) -> AskResult | None:
    """Answers `question` from the graph, or returns None when no LLM is configured."""
    if not config.llm.enabled or (ledger is not None and not ledger.can_spend()):
        return None
    client = get_client(config, timeout=config.ask.timeout_seconds)
    if client is None:
        return None
    intent, queries, needs_all = plan_question(question, config, client, ledger, as_of)
    searches = [question] + [q for q in queries if q.lower() != question.lower()]
    results = gather(store, searches, config.ask.top_k)
    if not results:
        return AskResult(answer="No relevant knowledge found.", intent=intent, queries=searches)
    context, used, chosen = assemble(results, standing_directives(store), config.ask.context_tokens)
    cfg = config.ask
    user = (f"Today's date: {_today(as_of)}\n\n{context}\n\nQuestion: {question}\n\n"
            f"{STYLE.get(intent, DEFAULT_STYLE)}{READINGS if cfg.readings else ''}")

    def sample(i: int) -> str:
        return _call(client, config, system=ANSWER_SYSTEM, user=user, effort=cfg.effort,
                     max_tokens=cfg.max_output_tokens, ledger=ledger, purpose="ask", sample=i)

    answers = []
    for i in range(max(1, cfg.samples)):
        try:
            text = sample(i)
        except Exception:
            if i == 0:
                raise
            continue
        if text:
            answers.append(text)
    if not answers:
        return None
    final = answers[0]
    if len(answers) > 1:
        listing = "".join(f"Candidate {i + 1}:\n{a}\n\n" for i, a in enumerate(answers))
        try:
            picked = _call(client, config, system=ANSWER_SYSTEM, ledger=ledger, purpose="ask-adjudicate",
                           user=f"{user}\n\nSeveral candidate answers were drafted independently:\n\n{listing}"
                                "Check them against the evidence. Pick the best-supported one (prefer the "
                                "answer most candidates agree on unless the evidence shows it is wrong) and "
                                "give the final answer.",
                           effort=cfg.effort, max_tokens=cfg.max_output_tokens)
            final = picked or final
        except Exception:
            pass
    return AskResult(answer=final, intent=intent, queries=searches, context=context,
                     context_tokens=used, results=chosen)
