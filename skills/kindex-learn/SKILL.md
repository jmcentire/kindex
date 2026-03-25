---
name: kindex-learn
description: Extract knowledge from the current conversation and add it to the Kindex graph. Use at session end or after producing valuable discoveries, decisions, or insights.
---

# Kindex Learn

Extract knowledge from the current conversation and add it to the graph.

## Instructions

1. Summarize the key discoveries, decisions, and concepts from this session in 2-3 paragraphs
2. Call the `learn` MCP tool with this summary text
3. If specific decisions were made, call `add` for each one with `node_type: "decision"`
4. If new connections between existing concepts were discovered, call `link` to create edges
5. Report what was captured: number of new nodes, links, and any bridge suggestions

Focus on *what matters* — not every line of code, but the insights, patterns, and decisions that would help a future session.
