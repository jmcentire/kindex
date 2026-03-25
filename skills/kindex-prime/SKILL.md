---
name: kindex-prime
description: Load relevant context from the Kindex knowledge graph at session start. Orients Claude with project knowledge, active constraints, and watches.
---

# Kindex Prime

Load relevant context from the knowledge graph at the start of a session.

## Instructions

1. Call the `context` MCP tool with the current project topic
2. If no topic is clear from the working directory, call `status` to get an overview
3. Review any active constraints or watches that may be relevant
4. Present a brief orientation to the user: what Kindex knows about this project, key connections, and any operational nodes that apply

Keep the orientation concise (3-5 lines). The user wants to know what's relevant, not a full dump.
