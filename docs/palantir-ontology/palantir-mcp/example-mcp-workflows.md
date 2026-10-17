# Palantir Foundry Docs — /docs/foundry/palantir-mcp/example-mcp-workflows

> 来源: https://www.palantir.com/docs/foundry/palantir-mcp/example-mcp-workflows
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Example MCP workflows

This page contains examples of MCP workflows, which are a linked series of actions used by an AI agent to achieve a goal. MCP workflows can involve tool use as well as connection to resources like repositories.

## Repository context discovery

The Palantir MCP is able to provide ontology and code-snippet context to your agent loop. After the context is provided, the agent will be able to perform the following:

- Navigate your code base and use internal Palantir libraries.
- Navigate the remote Foundry project and investigate resources within the project.

Example queries:

- "Fetch code/API context for my Foundry project"
- "Use the Palantir MCP to provide context on this project"
- "Tell me about my repository"

The Palantir MCP currently provides context for `React OSDK`, `Python transform`, and `Typescript function` repositories.

![Repository context discovery diagram.](/docs/resources/foundry/palantir-mcp/repository-context-discovery-loop.png)

## Dataset search and creation

The Palantir MCP is able to list datasets, run SQL queries on them, and create datasets with notional data.

The Palantir MCP cannot overwrite existing datasets and can only create new ones, which provides a safeguard for your existing data.

Example queries:

- Explore: "What Foundry Datasets do I have access to?"
- Investigate: "Show me the top delayed Flights in /Path/to/dataset/Flight\_routes."
- Investigate: "Is this dataset clean? `ri.foundry.main.dataset.033384ec-73de-41c1-bebe-45178cfc468b`"
- Create: "Create me a Foundry dataset with notional data. It should have columns ..., and be at least 50 rows."
- Create: "Create me a Foundry dataset with notional data. Use a script to generate 10,000 rows of data."

![Dataset search and creation diagram.](/docs/resources/foundry/palantir-mcp/dataset-search-and-creation-loop.png)

## Ontology exploration

The Palantir MCP has tools for searching the ontology. The tools allow agents to search through object types, action types, and functions in your ontology.

Example queries:

- "Find me an object type {description}."
- "Find or create me an object type {description}."
- "What functions interact with this object?"
- "Find me functions {description}."

![Ontology exploration diagram.](/docs/resources/foundry/palantir-mcp/ontology-exploration-loop.png)

## Ontology modification and SDK generation

The Palantir MCP can create proposals, modify the ontology, and regenerate your Developer Console OSDK for immediate use in code.

All modifications to the ontology must go through the [proposal](/docs/foundry/ontologies/branching-ontology/) review process, which means that human review and approval are required before the MCP makes any lasting changes to the ontology.

This flow is initiated by ontology-related queries, such as the following:

- "Create an object type {description}"
- "Create a link type {description}"
- "Create an object type {description} and link it to {description}"
- "Create a one-to-many link between {object 1} and {object 2}"
- "Create a many-to-many link between {object 1} and {object 2}"

### SDK generation

![Ontology modification and SDK generation diagram.](/docs/resources/foundry/palantir-mcp/ontology-modification-and-sdk-generation-loop.png)

### Object type creation

![Object type creation diagram.](/docs/resources/foundry/palantir-mcp/object-type-creation-loop.png)

### Link type creation

![Link type creation diagram.](/docs/resources/foundry/palantir-mcp/link-type-creation-loop.png)
