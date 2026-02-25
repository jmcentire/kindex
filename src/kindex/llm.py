"""Optional LLM integration for classification and smart search.

Falls back gracefully to keyword matching when LLM is unavailable or over budget.
"""

from __future__ import annotations

from .budget import BudgetLedger
from .config import Config

# Authoritative pricing per token (cache-aware)
PRICING = {
    "claude-haiku-4-5-20251001": {
        "input": 0.80e-6, "output": 4.00e-6,
        "cache_write": 1.00e-6, "cache_read": 0.08e-6,
    },
    "claude-sonnet-4-6": {
        "input": 3.00e-6, "output": 15.00e-6,
        "cache_write": 3.75e-6, "cache_read": 0.30e-6,
    },
    "claude-opus-4-6": {
        "input": 15.00e-6, "output": 75.00e-6,
        "cache_write": 18.75e-6, "cache_read": 1.50e-6,
    },
}
_DEFAULT_PRICE = {
    "input": 1.00e-6, "output": 5.00e-6,
    "cache_write": 1.25e-6, "cache_read": 0.10e-6,
}


def get_client(config: Config):
    """Get anthropic client, or None if not available."""
    if not config.llm.enabled:
        return None
    try:
        import os
        import anthropic
        api_key = os.environ.get(config.llm.api_key_env)
        if not api_key:
            import sys
            print(f"Warning: LLM enabled but {config.llm.api_key_env} not set. "
                  f"Falling back to keyword matching.", file=sys.stderr)
            return None
        return anthropic.Anthropic(api_key=api_key)
    except ImportError:
        import sys
        print("Warning: LLM enabled but 'anthropic' package not installed. "
              "Install with: pip install kindex[llm]", file=sys.stderr)
        return None


# Backward-compatible alias
_get_client = get_client


def calculate_cost(model: str, usage) -> dict:
    """Calculate cost from Anthropic response usage, cache-aware."""
    p = PRICING.get(model, _DEFAULT_PRICE)
    tokens_in = getattr(usage, "input_tokens", 0)
    tokens_out = getattr(usage, "output_tokens", 0)
    cache_write = getattr(usage, "cache_creation_input_tokens", 0)
    cache_read = getattr(usage, "cache_read_input_tokens", 0)
    amount = (
        tokens_in * p["input"]
        + cache_write * p["cache_write"]
        + cache_read * p["cache_read"]
        + tokens_out * p["output"]
    )
    return {
        "amount": round(amount, 8),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "cache_creation_tokens": cache_write,
        "cache_read_tokens": cache_read,
    }


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Legacy cost estimation (no cache awareness)."""
    pricing = PRICING.get(model, _DEFAULT_PRICE)
    return tokens_in * pricing["input"] + tokens_out * pricing["output"]


def classify_for_graph(
    text: str,
    existing_slugs: list[str],
    config: Config,
    ledger: BudgetLedger,
) -> dict | None:
    """Ask LLM to classify text into relevant topics/skills.

    Returns dict with keys: topics, skills, suggested_title, suggested_tags
    Returns None if LLM unavailable or over budget.
    """
    if not ledger.can_spend():
        return None

    client = _get_client(config)
    if client is None:
        return None

    slugs_str = ", ".join(existing_slugs[:50])
    prompt = f"""Given this information from a conversation:

"{text}"

And these existing knowledge graph nodes: {slugs_str}

Respond with ONLY a YAML block:
```yaml
related_topics: [list of existing slugs that relate, max 5]
new_topic_slug: suggested-slug-if-new  # or empty string if fits existing
suggested_title: "Short title"
suggested_tags: [tag1, tag2]
is_skill: false  # true if this describes an ability/capability
```"""

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}],
        )

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = _estimate_cost(config.llm.model, tokens_in, tokens_out)
        ledger.record(cost, model=config.llm.model, purpose="classify",
                      tokens_in=tokens_in, tokens_out=tokens_out)

        # Parse YAML from response
        import yaml
        text_out = response.content[0].text
        # Extract yaml block
        if "```yaml" in text_out:
            text_out = text_out.split("```yaml")[1].split("```")[0]
        elif "```" in text_out:
            text_out = text_out.split("```")[1].split("```")[0]

        return yaml.safe_load(text_out)
    except Exception:
        return None


def smart_search(
    query: str,
    existing_slugs: list[str],
    config: Config,
    ledger: BudgetLedger,
) -> list[str] | None:
    """Ask LLM to pick the most relevant slugs for a query.

    Returns list of slugs, or None if LLM unavailable.
    """
    if not ledger.can_spend():
        return None

    client = _get_client(config)
    if client is None:
        return None

    slugs_str = ", ".join(existing_slugs)
    prompt = f"""From these knowledge graph nodes: {slugs_str}

Which are most relevant to this query: "{query}"

Respond with ONLY a comma-separated list of slugs, most relevant first. Max 10."""

    try:
        response = client.messages.create(
            model=config.llm.model,
            max_tokens=150,
            messages=[{"role": "user", "content": prompt}],
        )

        tokens_in = response.usage.input_tokens
        tokens_out = response.usage.output_tokens
        cost = _estimate_cost(config.llm.model, tokens_in, tokens_out)
        ledger.record(cost, model=config.llm.model, purpose="search",
                      tokens_in=tokens_in, tokens_out=tokens_out)

        text_out = response.content[0].text.strip()
        slugs = [s.strip() for s in text_out.split(",")]
        return [s for s in slugs if s in existing_slugs]
    except Exception:
        return None
