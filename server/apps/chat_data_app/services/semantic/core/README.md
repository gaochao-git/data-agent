# Semantic Core

`semantic/core` contains framework-independent semantic validation and SQL compilation primitives.

The package may depend on the Python standard library and its own modules only. It must not depend on ChatData services, database sessions, data source managers, Agent state, LangChain, or LLMs.

Business services adapt stored semantic configurations and user-facing Builder or DSL payloads before calling this package. Query execution, permissions, model loading, and response assembly remain outside the core.
