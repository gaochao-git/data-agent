# opencode_runtime (skeleton)

Minimal, non-invasive runtime skeleton for an OpenCode-style architecture in the complaint app.
This directory contains registry and data-model scaffolding only; it does not replace existing
business logic or API routes.

## Scope
- Session + Message + Part data structures with serialization helpers.
- Agent registry with a lightweight Agent interface.
- Tool registry with permission checks and execution context.
- Skill loader that reads markdown templates from a skills folder.
- Permission engine with allow/deny/ask decisions.
- Compaction interfaces (token estimation + stub compactor).
- Runtime config from environment variables.

## Intended usage
Import these modules and progressively wire them into existing pipelines (chat, analysis,
summary, extraction). The current complaint app logic stays intact unless explicitly integrated.
