---
name: kindex-capture
description: Capture a specific piece of knowledge, constraint, or decision to the Kindex knowledge graph. Use when the user says "remember this", "add this to kindex", or "this is important".
---

# Kindex Capture

Capture a specific piece of knowledge, constraint, or decision right now.

## Instructions

1. Identify what the user wants to capture
2. Call `search` for it. If a matching node already exists, change that node with `edit` (or `supersede` for decisions, constraints and directives) instead of adding a duplicate
3. Otherwise call the `add` MCP tool with the text and the `node_type` that fits:
   - `concept` — general knowledge or insight
   - `decision` — a choice that was made and why
   - `constraint` — an invariant that must always hold ("never do X")
   - `directive` — a soft rule or preference ("prefer Y over Z")
   - `question` — an open question to revisit later
   - `skill` — a capability or competency
4. `link` the node to related nodes the search found
5. Confirm to the user what was captured and any connections made
