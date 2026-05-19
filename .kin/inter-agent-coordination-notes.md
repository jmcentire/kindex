# Inter-Agent Coordination Notes

2026-05-19

Working frame after Simulacrum/Advocate pass:

- Do not model this as durable "conversation tags." The stronger primitive is a short-lived coordination plane.
- Coordination state is operational state: claims, messages, handoffs, and liveness-ish expiry. It should not become long-term knowledge unless an agent explicitly promotes a discovery/decision through normal Kindex capture.
- Task claims are expiring metadata on existing task nodes. Expiry prevents zombie locks when an agent dies or abandons a branch of work.
- Coordination conversations are active `coordination` nodes while live, but `coord_end`/cleanup archives them and clears message bodies. A retained summary can remain when useful.

Initial API shape:

- `kin task claim --task-id <id> --agent <name> [--ttl N] [--force]`
- `kin task release --task-id <id> [--agent <name>] [--force]`
- `kin coord start|post|read|list|end|cleanup`
- MCP tools mirror this: `task_claim`, `task_release`, `coord_start`, `coord_post`, `coord_read`, `coord_list`, `coord_end`.
