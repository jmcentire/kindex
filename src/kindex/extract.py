"""LLM extraction pipeline — extracts structured knowledge from raw text.

Budget-aware: falls back to keyword extraction when over limit.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .budget import BudgetLedger
from .config import Config

# Haiku pricing per million tokens
_PRICING = {
    "claude-haiku-4-5-20251001": {"input": 0.80 / 1_000_000, "output": 4.00 / 1_000_000},
}
_DEFAULT_PRICE = {"input": 1.00 / 1_000_000, "output": 5.00 / 1_000_000}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    p = _PRICING.get(model, _DEFAULT_PRICE)
    return tokens_in * p["input"] + tokens_out * p["output"]


def _get_client(config: Config):
    if not config.llm.enabled:
        return None
    try:
        import os
        import anthropic
        key = os.environ.get(config.llm.api_key_env)
        if not key:
            import sys
            print(f"Warning: LLM enabled but {config.llm.api_key_env} not set. "
                  f"Falling back to keyword extraction.", file=sys.stderr)
            return None
        return anthropic.Anthropic(api_key=key)
    except ImportError:
        import sys
        print("Warning: LLM enabled but 'anthropic' package not installed. "
              "Install with: pip install kindex[llm]", file=sys.stderr)
        return None


# ── LLM extraction ─────────────────────────────────────────────────────

EXTRACT_PROMPT = """Analyze this text and extract structured knowledge.

TEXT:
{text}

EXISTING GRAPH NODES (for linking, not duplication):
{existing_titles}

Return ONLY valid JSON:
{{
  "concepts": [
    {{"title": "short clear title", "content": "1-3 sentence summary", "domains": ["domain"], "type": "concept"}}
  ],
  "decisions": [
    {{"title": "what was decided", "rationale": "why", "type": "decision"}}
  ],
  "questions": [
    {{"question": "open question text", "context": "what prompted it", "type": "question"}}
  ],
  "connections": [
    {{"from_title": "source node", "to_title": "target node", "type": "relates_to", "why": "reason"}}
  ],
  "bridge_opportunities": [
    {{"concept_a": "node A", "concept_b": "node B", "potential_link": "why they might connect"}}
  ]
}}

Rules:
- Prefer linking to EXISTING nodes over creating new ones
- Only create new concept nodes for genuinely new ideas
- Bridge opportunities are cross-domain connections the user didn't explicitly make
- Keep titles plain and boring — good for retrieval, not for display
- Decisions must have rationale
- Questions must be genuinely open (not rhetorical)"""


def llm_extract(
    text: str,
    existing_titles: list[str],
    config: Config,
    ledger: BudgetLedger,
) -> dict | None:
    """Run LLM extraction pass. Returns structured dict or None if unavailable."""
    if not ledger.can_spend():
        return None

    client = _get_client(config)
    if client is None:
        return None

    titles_str = ", ".join(existing_titles[:100])
    prompt = EXTRACT_PROMPT.format(text=text[:4000], existing_titles=titles_str)

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )

        cost = _estimate_cost(
            config.llm.model,
            response.usage.input_tokens,
            response.usage.output_tokens,
        )
        ledger.record(cost, model=config.llm.model, purpose="extract",
                      tokens_in=response.usage.input_tokens,
                      tokens_out=response.usage.output_tokens)

        text_out = response.content[0].text
        # Extract JSON from response
        if "```json" in text_out:
            text_out = text_out.split("```json")[1].split("```")[0]
        elif "```" in text_out:
            text_out = text_out.split("```")[1].split("```")[0]

        return json.loads(text_out)
    except Exception:
        return None


# ── Keyword fallback ───────────────────────────────────────────────────

def keyword_extract(text: str) -> dict:
    """Simple keyword-based extraction when LLM unavailable.

    Extracts potential concepts from capitalized phrases, quoted terms,
    and structural patterns.
    """
    concepts = []
    connections = []

    # Find capitalized multi-word phrases (likely proper nouns / concepts)
    phrases = re.findall(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', text)
    seen = set()
    for phrase in phrases:
        lower = phrase.lower()
        if lower not in seen and len(phrase) > 5:
            seen.add(lower)
            concepts.append({
                "title": phrase,
                "content": "",
                "domains": [],
                "type": "concept",
            })

    # Find quoted terms
    quoted = re.findall(r'"([^"]{3,50})"', text)
    for q in quoted:
        lower = q.lower()
        if lower not in seen:
            seen.add(lower)
            concepts.append({
                "title": q,
                "content": "",
                "domains": [],
                "type": "concept",
            })

    # Detect connection language
    conn_patterns = [
        r'(?:similar to|same as|connects to|related to|reminds me of)\s+["\']?(\w[\w\s]{2,30})',
        r'(\w[\w\s]{2,30})\s+(?:is like|is related to|is similar to)',
    ]
    for pattern in conn_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for m in matches:
            connections.append({
                "from_title": "",  # needs resolution
                "to_title": m.strip(),
                "type": "relates_to",
                "why": "detected in text",
            })

    return {
        "concepts": concepts[:10],
        "decisions": [],
        "questions": [],
        "connections": connections[:5],
        "bridge_opportunities": [],
    }


def extract(
    text: str,
    existing_titles: list[str],
    config: Config,
    ledger: BudgetLedger,
) -> dict:
    """Extract knowledge — LLM if available, keyword fallback otherwise."""
    result = llm_extract(text, existing_titles, config, ledger)
    if result is not None:
        return result
    return keyword_extract(text)
