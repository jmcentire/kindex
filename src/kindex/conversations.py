"""Conversation ingest: a transcript becomes dated document nodes, losslessly.

Session ingest (`kin ingest sessions`) keeps a summary of the first 8,000
characters, and `kin add` keeps what its extractor picked out. Questions about
past conversations ask for the details those drop: an amount, a date, a name,
what the assistant recommended. Here every message is kept, whole messages are
packed into `document` nodes of at most `node_chars` characters (the cap file
ingest uses), each node carries the conversation's date as `prov_when`, and the
nodes of one conversation are linked in order so graph expansion can reach a
node's neighbours.

With an LLM configured, `digest_conversation` also reads each conversation once
(and, with `conversations.facts`, writes down its dated facts as nodes):
the user's standing instructions (how the assistant should respond from now on)
become `directive` nodes, which `kin ask` shows with every answer, and a dated
summary becomes a node linked to the conversation, for questions that span many
conversations.

A conversation file is JSON or JSON Lines; each object is
``{"id": ..., "date": ..., "messages": [{"role": ..., "content": ..., "name": ...}]}``.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from .store import Store

NODE_CHARS = 4000


def _lines(messages: list[dict]) -> list[str]:
    out = []
    for m in messages:
        who = m.get("name") or m.get("role") or "user"
        text = str(m.get("content") or "").strip()
        if text:
            out.append(f"{who}: {text}")
    return out


def pack(lines: list[str], limit: int = NODE_CHARS) -> list[str]:
    """Whole messages packed into chunks of at most `limit` characters; a longer message is split."""
    nodes, cur = [], ""
    for line in lines:
        while len(line) > limit:
            if cur:
                nodes.append(cur)
                cur = ""
            nodes.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            nodes.append(cur)
            cur = ""
        cur = f"{cur}\n{line}" if cur else line
    if cur:
        nodes.append(cur)
    return nodes


def ingest_conversation(store: Store, conversation_id: str, messages: list[dict], when: str | None = None,
                        *, node_chars: int = NODE_CHARS, prov_source: str | None = None) -> list[str]:
    """Stores one conversation; returns the ids of the nodes it created. Re-ingest is idempotent."""
    created: list[str] = []
    for i, text in enumerate(pack(_lines(messages), node_chars)):
        node_id = "conv-" + hashlib.sha256(f"{conversation_id}|{i}".encode()).hexdigest()[:16]
        if store.get_node(node_id):
            continue
        title = text[:60].strip() + ("..." if len(text) > 60 else "")
        store.add_node(
            node_id=node_id,
            title=title,
            content=text,
            node_type="document",
            prov_source=prov_source or conversation_id,
            prov_activity="conversation-ingest",
            prov_when=when,
            extra={"conversation_id": conversation_id, "position": i},
        )
        created.append(node_id)
    for a, b in zip(created, created[1:]):
        store.add_edge(a, b, edge_type="relates_to", provenance="next in conversation")
    return created


def load_conversations(path: Path) -> list[dict]:
    text = path.read_text()
    try:
        data = json.loads(text)
        return data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def ingest_directory(store: Store, directory: Path, verbose: bool = False) -> int:
    count = 0
    for path in sorted(list(directory.rglob("*.json")) + list(directory.rglob("*.jsonl"))):
        for conv in load_conversations(path):
            cid = str(conv.get("id") or f"{path.stem}")
            created = ingest_conversation(store, cid, conv.get("messages") or [], conv.get("date"),
                                          prov_source=str(path))
            count += len(created)
            if verbose and created:
                print(f"  Conversation {cid}: {len(created)} node(s)")
    return count


DIGEST_SCHEMA = {
    "name": "conversation_digest",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["directives", "summary"],
        "properties": {
            "directives": {"type": "array", "items": {"type": "string"}},
            "summary": {"type": "string"},
        },
    },
}

DIRECTIVES_ONLY_SCHEMA = {
    "name": "conversation_digest",
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["directives"],
        "properties": {"directives": {"type": "array", "items": {"type": "string"}}},
    },
}

# A summary earns its place only when it compresses: a short conversation is its own summary.
SUMMARY_MIN_TOKENS = 6000

DIGEST_PROMPT = """Below is {what}{date}. Return:

- directives: the user's standing instructions: requests about how the assistant should respond to future requests, stated as such ("always ...", "from now on ...", "whenever I ask about ..."), covering formats, things to always include or avoid, style, units or level of detail. Write each as one imperative sentence that keeps the user's specifics ("Always include type hints in Python examples"). Instructions that configure the task at hand are not standing instructions: how to write this piece, which language or spelling to use for it, a role to play, a game, quiz, exercise or classification to run for the rest of the conversation ("for every sentence I give you, reply only with ...", "respond only with OK until I say done"). Often there are none.
{summary}
Conversation:
{text}"""

# Facts written down once, when the conversation is read: dated, self-contained
# statements a later question can be answered from without re-deriving
# "last Saturday" or a count from raw text at answer time.
FACTS_FIELD = """- facts: everything in the conversation worth remembering about the user and their world, so a question months later can be answered without the original text. One fact per item, each self-contained: name the people, places, items and titles instead of using pronouns, and keep numbers, amounts, counts and units. For each fact give: date, when it happened or was true, as YYYY-MM-DD or a period (2023-05, "the week before 2023-06-12"), resolving relative dates ("last Saturday", "two weeks ago", "tomorrow") against the conversation date, or empty if undated; subject, who it is about ("user", "assistant", or a person's name); text, the fact in one sentence, keeping the original relative phrase when one was used ("the user bought a red kayak for $450 on the Saturday before 2023-05-20"). Include events and activities, purchases, plans with dates, possessions and counts, preferences and opinions, relationships, health, work and places, changes ("the user now walks 10,000 steps a day; earlier it was 5,000"), and the specific recommendations, lists and answers the assistant gave. Leave out small talk and generic advice.
"""

FACT_ITEM = {
    "type": "object",
    "additionalProperties": False,
    "required": ["date", "subject", "text"],
    "properties": {"date": {"type": "string"}, "subject": {"type": "string"}, "text": {"type": "string"}},
}


def _digest_schema(summary: bool, facts: bool) -> dict:
    props = {"directives": {"type": "array", "items": {"type": "string"}}}
    if summary:
        props["summary"] = {"type": "string"}
    if facts:
        props["facts"] = {"type": "array", "items": FACT_ITEM}
    return {"name": "conversation_digest",
            "schema": {"type": "object", "additionalProperties": False, "required": list(props), "properties": props}}


SUMMARY_FIELD = """- summary: 4 to 8 sentences on what the conversation covered: the user's situation, the facts, figures, decisions and plans they gave, and what the assistant advised, with dates (resolve "last week" or "tomorrow" against the conversation date).
"""


_SPEAKER = re.compile(r"^(user|assistant|system): ", re.M)


def user_messages(text: str) -> str:
    """The user's messages from conversation text written as "role: message" lines
    (the form ingest_conversation stores); empty when the text has no user role."""
    parts = _SPEAKER.split(text)
    # split() yields [before, role, body, role, body, ...]
    return "\n".join(f"user: {body.strip()}" for role, body in zip(parts[1::2], parts[2::2])
                     if role == "user" and body.strip())


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def digest_conversation(store: Store, conversation_id: str, text: str, when: str | None, config, ledger=None,
                        *, effort: str = "low", link_to: list[str] | None = None,
                        summary_min_tokens: int | None = None) -> dict:
    """One model pass over a conversation: its standing instructions become `directive`
    nodes, and, for a conversation of at least `summary_min_tokens`, its summary a
    `document` node linked to the conversation's nodes."""
    from .answer import _call
    from .llm import get_client

    out = {"directives": [], "summary": None}
    if not config.llm.enabled or (ledger is not None and not ledger.can_spend()):
        return out
    client = get_client(config, timeout=config.ask.timeout_seconds)
    if client is None:
        return out
    threshold = SUMMARY_MIN_TOKENS if summary_min_tokens is None else summary_min_tokens
    summarize = len(text) // 4 >= threshold
    facts = bool(getattr(getattr(config, "conversations", None), "facts", False))
    what = "a conversation between a user and an assistant"
    if not summarize and not facts:
        # Directives are the user's: a short conversation is read from the user's side only.
        mine = user_messages(text)
        if mine:
            text, what = mine, "what a user wrote in a conversation with an assistant"
    fields = (SUMMARY_FIELD if summarize else "") + (FACTS_FIELD if facts else "")
    schema = _digest_schema(summarize, True) if facts else (DIGEST_SCHEMA if summarize else DIRECTIVES_ONLY_SCHEMA)
    raw = _call(client, config, system=None, ledger=ledger, purpose="conversation-digest",
                user=DIGEST_PROMPT.format(what=what, date=f" on {when}" if when else "", text=text,
                                          summary=fields),
                effort=effort, max_tokens=24000 if facts else 8000, json_schema=schema)
    try:
        digest = json.loads(raw)
    except json.JSONDecodeError:
        return out
    existing = {_norm(n.get("title") or "") for n in store.all_nodes(node_type="directive", limit=1000)}
    for d in digest.get("directives") or []:
        d = str(d).strip()
        if not d or _norm(d) in existing:
            continue
        existing.add(_norm(d))
        out["directives"].append(store.add_node(
            title=d, content="", node_type="directive", prov_source=conversation_id,
            prov_activity="conversation-digest", prov_when=when))
    summary = str(digest.get("summary") or "").strip() if summarize else ""
    if summary:
        nid = "convsum-" + hashlib.sha256(conversation_id.encode()).hexdigest()[:16]
        if not store.get_node(nid):
            store.add_node(node_id=nid, title=f"Summary of a conversation{f' on {when}' if when else ''}",
                           content=summary, node_type="document", prov_source=conversation_id,
                           prov_activity="conversation-digest", prov_when=when,
                           extra={"conversation_id": conversation_id, "kind": "conversation-summary"})
            for target in link_to or []:
                store.add_edge(nid, target, edge_type="relates_to", provenance="summary of conversation")
        out["summary"] = nid
    out["facts"] = []
    for i, fact in enumerate((digest.get("facts") or []) if facts else []):
        text_ = str((fact or {}).get("text") or "").strip()
        if not text_:
            continue
        nid = "convfact-" + hashlib.sha256(f"{conversation_id}|{i}".encode()).hexdigest()[:16]
        if store.get_node(nid):
            continue
        store.add_node(node_id=nid, title=text_[:60].strip() + ("..." if len(text_) > 60 else ""),
                       content=text_, node_type="document", prov_source=conversation_id,
                       prov_activity="conversation-digest", prov_when=when,
                       extra={"conversation_id": conversation_id, "kind": "conversation-fact",
                              "fact_date": str(fact.get("date") or "").strip(),
                              "subject": str(fact.get("subject") or "").strip()})
        for target in link_to or []:
            store.add_edge(nid, target, edge_type="relates_to", provenance="fact from conversation")
        out["facts"].append(nid)
    return out


def backfill_digests(store: Store, config, ledger=None, *, max_chars: int = 400_000,
                     activities: tuple[str, ...] = ("conversation-ingest",)) -> int:
    """Digests conversations already in the graph, one call per conversation.

    Conversations are the document nodes whose `prov_activity` is in `activities`,
    grouped by `prov_source`. Returns the number of conversations digested."""
    convs: dict[str, list[dict]] = {}
    for node in store.all_nodes(node_type="document", limit=1_000_000):
        if node.get("prov_activity") in activities:
            convs.setdefault(node.get("prov_source") or node["id"], []).append(node)
    done = set(json.loads(store.get_meta("conversation_digests") or "[]"))
    count = 0
    for cid, nodes in convs.items():
        if cid in done:
            continue
        nodes.sort(key=lambda n: ((n.get("extra") or {}).get("position", 0), n.get("created_at") or ""))
        text = "\n".join(n.get("content") or "" for n in nodes)[:max_chars]
        digest_conversation(store, cid, text, nodes[0].get("prov_when"), config, ledger,
                            link_to=[n["id"] for n in nodes])
        done.add(cid)
        store.set_meta("conversation_digests", json.dumps(sorted(done)))
        count += 1
    return count
