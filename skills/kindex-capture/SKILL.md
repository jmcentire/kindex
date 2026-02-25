# Kindex Capture

Capture a specific piece of knowledge, constraint, or decision right now.

## Usage

Run this skill when the user says something like "remember this", "add this to kindex", or "this is important".

## Instructions

1. Identify what the user wants to capture
2. Determine the appropriate node type:
   - `concept` — general knowledge or insight
   - `decision` — a choice that was made and why
   - `constraint` — an invariant that must always hold ("never do X")
   - `directive` — a soft rule or preference ("prefer Y over Z")
   - `question` — an open question to revisit later
   - `skill` — a capability or competency
3. Call the `add` MCP tool with the text and appropriate type
4. If the knowledge relates to existing nodes, call `search` first and then `link` to connect them
5. Confirm to the user what was captured and any connections made
