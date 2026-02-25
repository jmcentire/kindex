# Kindex Learn

Extract knowledge from the current conversation and add it to the graph.

## Usage

Run this skill when a session has produced valuable knowledge, decisions, or discoveries that should be preserved.

## Instructions

1. Summarize the key discoveries, decisions, and concepts from this session in 2-3 paragraphs
2. Call the `learn` MCP tool with this summary text
3. If specific decisions were made, call `add` for each one with `node_type: "decision"`
4. If new connections between existing concepts were discovered, call `link` to create edges
5. Report what was captured: number of new nodes, links, and any bridge suggestions

Focus on *what matters* — not every line of code, but the insights, patterns, and decisions that would help a future session.
