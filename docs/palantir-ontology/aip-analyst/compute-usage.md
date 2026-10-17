# Palantir Foundry Docs — /docs/foundry/aip-analyst/compute-usage

> 来源: https://www.palantir.com/docs/foundry/aip-analyst/compute-usage
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Compute usage with AIP Analyst

AIP Analyst is an agentic application: a single question can result in many model calls and many queries against Foundry. Understanding where that usage comes from helps you predict cost, choose a model, and design efficient analyses.

Usage from AIP Analyst falls into two categories:

- **LLM tokens:** Every model call consumes input and output tokens, which are converted into compute-seconds at a rate that depends on the model. See [LLM token usage](#llm-token-usage).
- **Federated Foundry compute:** Each tool call runs against an underlying Foundry system, such as the Ontology, a dataset, or a function, and consumes that system's compute in addition to the tokens spent asking for and reading the result. See [Federated Foundry compute](#federated-foundry-compute).

If you have an enterprise contract with Palantir, contact your Palantir representative before proceeding with compute usage calculations.

## LLM token usage

AIP Analyst works by repeatedly sending the conversation to a model and acting on the response. Each request includes the system instructions, your messages, the definitions of the enabled tools, and the results of earlier tool calls that are still in [context](/docs/foundry/aip-analyst/using-aip-analyst/#context). The model's reply, including any extended thinking, counts as output tokens.

Three consequences follow from this design:

- **Token usage grows as a conversation continues,** because earlier messages and tool results are resent with each new turn. A long analysis costs more per question at the end than at the beginning.
- **Large tool results are the dominant driver.** Loading thousands of objects, returning a wide SQL result set, or reading a long PDF places a large amount of text into context, where it is resent on every subsequent turn until it is removed.
- **The set of enabled tools has a cost of its own,** because tool definitions are included in each request. Disabling categories you do not need in the **Tools** menu reduces the size of every request in the analysis.

Token-to-compute-second rates vary significantly between models. For the current rates, see [Usage-based pricing compute translation for AIP](/docs/foundry/aip/aip-compute-usage/#usage-based-pricing-compute-translation-for-aip). For general background on how tokens are counted, see [Tokens in AIP](/docs/foundry/aip/aip-compute-usage/#tokens-in-aip).

A saved analysis retains its selected model category or specific model. The model used by a category can change over time, which can also change token-to-compute-second rates. See [Per-analysis settings](/docs/foundry/aip-analyst/analysis-resources/#per-analysis-settings).

## Federated Foundry compute

Most AIP Analyst tools do not perform computation themselves. Instead, they call an existing Foundry system on your behalf, and that system's usual compute model applies. The following table maps each [tool category](/docs/foundry/aip-analyst/capabilities/) to the system that performs the work.

| Tool category | Work performed by | Usage documentation |
| --- | --- | --- |
| Ontology | Ontology query layer | [Compute usage with Ontology queries](/docs/foundry/ontologies/query-compute-usage/) |
| Actions | Ontology writeback | [Ontology Query Compute](/docs/foundry/ontologies/query-compute-usage/#ontology-query-compute-2026) |
| Datasets | SQL against datasets | [Compute usage with SQL in Foundry](/docs/foundry/sql-warehousing/compute-usage/) |
| Functions | Function execution | [Understand function compute costs](/docs/foundry/functions/optimize-performance/#understand-function-compute-costs) |
| Time series | Time series query layer | [Time series query compute usage](/docs/foundry/time-series/compute-usage/) |
| Media | Media set transformations and downloads | [Media set compute usage](/docs/foundry/media-sets-advanced-formats/media-usage-limits/) |
| Contour | Contour analyses | [Compute usage with Contour](/docs/foundry/contour/compute-usage/) |
| Quiver and Notepad | The applications embedded in the resource, such as Ontology queries | [List of Foundry applications and associated usage](/docs/foundry/resource-management/usage-types/#list-of-foundry-applications-and-associated-usage) |
| Workshop | Workshop module configuration metadata | [Compute usage with AIP](/docs/foundry/aip/aip-compute-usage/) |
| Machinery | Machinery process graph metadata | [Compute usage with AIP](/docs/foundry/aip/aip-compute-usage/) |
| Visualization and Planning | The model only | [Compute usage with AIP](/docs/foundry/aip/aip-compute-usage/) |

Tools in the **Visualization** and **Planning** categories, such as **Create visualization** and **Context cleanup**, do not query a separate backend. Their cost is the tokens spent producing and reading the result. The **Workshop lookup** and **Machinery lookup** tools behave the same way. Each reads a resource's configuration rather than running a query, so its cost is the tokens spent reading the returned definition. Any subsequent work on the object types, functions, or action types they surface is charged to the corresponding category.

AIP Analyst Ontology tools query the same Ontology layer as Object Explorer, Workshop, and the [Ontology SDK (OSDK)](/docs/foundry/ontology-sdk/overview/), and they are measured under the same model. An **Ontology aggregation** issued by AIP Analyst is charged as an aggregation query in the same way as the equivalent request from an OSDK application. What differs is the number of queries. An agent exploring an unfamiliar Ontology issues more searches and more discarded intermediate queries than an application built around a fixed set of known queries.

Resources AIP Analyst creates continue to consume compute after the analysis ends. A published Quiver dashboard or a saved Contour analysis uses compute each time someone opens it, attributed to that resource rather than to AIP Analyst.

## Cost attribution

Compute-seconds in Foundry are usually attributed to resources rather than to users. For AIP Analyst, token usage is attributed as follows:

- **Saved analyses:** Usage is attributed to the [analysis resource](/docs/foundry/aip-analyst/analysis-resources/).
- **Embedded analyses:** Usage is attributed to the owning resource, such as the Workshop module containing the [AIP Analyst widget](/docs/foundry/aip-analyst/workshop-widget/).
- **Unsaved analyses:** By default, usage is attributed to the user who ran the analysis. If the **Unsaved analysis cost attribution** setting is configured, usage is attributed to the project selected in that setting instead. See [General settings](/docs/foundry/aip-analyst/using-aip-analyst/#general).

Compute that AIP Analyst federates to other applications follows those applications' own attribution rules. For Ontology queries in particular, compute attaches to the resource where the query originated where one exists, and otherwise to the object types being queried. See [Investigating Foundry compute usage from Ontology queries](/docs/foundry/ontologies/query-compute-usage/#investigating-foundry-compute-usage-from-ontology-queries).

## Monitor your usage

You can review usage at several levels of granularity:

- **During an analysis:** The token estimator in the session shows the current context size. The [outline](/docs/foundry/aip-analyst/using-aip-analyst/#outline) reports token usage for each individual tool call, which makes it straightforward to identify the step that loaded too much data.
- **Across your enrollment:** Export the **AIP Token Usage** dataset from Control Panel for daily token consumption broken down by model and resource. See [Exporting AIP token usage data](/docs/foundry/aip/aip-compute-usage/#exporting-aip-token-usage-data).
- **Alongside other usage:** Review compute-seconds by project and resource in [Resource Management](/docs/foundry/resource-management/overview/), and track model requests over time with [AIP usage metrics](/docs/foundry/workflow-lineage/aip-usage-observability/#aip-usage-metrics).

## Manage your usage

The levers below reduce usage without changing the questions you can ask.

### Keep context small

- **Remove data you no longer need.** Use the [context cleanup](/docs/foundry/aip-analyst/capabilities/#planning) tool, or hide individual tool results from the outline, so large results stop being resent on every turn.
- **Branch instead of continuing.** When you move to an unrelated question, create a new tab rather than extending a long conversation. See [Tabs and branching](/docs/foundry/aip-analyst/using-aip-analyst/#tabs-and-branching).
- **Aggregate rather than load.** Prefer **Ontology aggregation** or **Ontology SQL** over loading a large object set and asking the model to summarize it. A single row of aggregated output costs a fraction of the objects behind it.

### Narrow the search space

- **Limit the search scope.** Restricting the Ontology, object type groups, projects, statuses, and visibilities reduces both the number of search queries the agent issues and the size of their results. See [Limit search scope](/docs/foundry/aip-analyst/using-aip-analyst/#limit-search-scope).
- **Load the right context in advance.** Adding the relevant object types or datasets up front removes exploratory search entirely. In the [Workshop widget](/docs/foundry/aip-analyst/workshop-widget/#input-configuration), preloading context and fixing the enabled tools makes the usage of a guided workflow far more predictable.
- **Disable unused tool categories.** Fewer enabled tools means smaller requests and fewer opportunities for the agent to explore an unproductive path.

### Choose an appropriate model

Rates differ by more than an order of magnitude between the largest and smallest models. Reserve the most capable models for genuinely difficult reasoning, and prefer smaller models for routine lookups and summarization. For embedded workflows where the question is known in advance, set the model explicitly and hide the model selector so usage stays predictable. See [View options](/docs/foundry/aip-analyst/embed/#view-options) and [Chat controls](/docs/foundry/aip-analyst/workshop-widget/#chat-controls).

## Integrated tools compared with functions and agents

When a workflow is well understood, you can move work out of the agent's tool loop and into a resource that AIP Analyst calls, or that runs entirely on its own. Doing so changes both the cost profile and the reliability of the result.

| Approach | How AIP Analyst uses it | Token cost | Best suited to |
| --- | --- | --- | --- |
| Integrated tools | The agent selects and calls tools directly, one step at a time | Highest: every intermediate result enters context, and each step needs a model call | Ad-hoc and exploratory questions, where the steps are not known in advance |
| Skills | The agent loads a set of reusable instructions, then uses integrated tools to follow them | Similar to integrated tools, with fewer wasted steps | Recurring analyses that follow a known approach but still need judgment |
| Functions | The agent calls the function once with **Execute function** and reads the returned value | Low: only the inputs and the returned value enter context | Deterministic, repeated computation, such as a business calculation or a fixed multi-step query |
| Pro-code agents | Triggered outside the session; results are read back from the Ontology | Separate from the analysis: the agent runs its own model loop | Long-running or autonomous work that does not need to complete inside an interactive session |

Use the following guidance to choose between them:

- **Prefer integrated tools while the question is still taking shape.** Their advantage is that no prior modeling is required, and the [graph](/docs/foundry/aip-analyst/using-aip-analyst/#graph) view makes each step auditable. Their cost is that intermediate data passes through the model's context.
- **Move logic into a [function](/docs/foundry/functions/overview/) once it stabilizes.** A function collapses what would be many tool calls into one. Its intermediate data never enters the analysis context, its result is deterministic rather than reasoned, and its compute is attributed to the function. This is usually the correct choice for a calculation you expect to repeat, and it is generally both cheaper and more reliable than asking the agent to derive the logic again each time.
- **Use a [skill](/docs/foundry/aip-analyst/using-aip-analyst/#skills) to capture an approach rather than a computation.** A skill records how to tackle a class of question. Because the agent still uses integrated tools to carry it out, a skill improves consistency but does not by itself reduce federated compute.
- **Use a [pro-code agent](/docs/foundry/agents/overview/) for work that should not block an interactive session.** Pro-code agents run asynchronously and are not subject to the synchronous function execution limit, which makes them appropriate for long-running tasks.

Pro-code agents are not a drop-in replacement for an AIP Analyst tool. A published agent function returns `Void`, so it cannot be called as a query function and cannot return a value into the analysis. Have the agent write its results to Ontology objects, then read those objects from AIP Analyst. See [Publish and call a pro-code agent](/docs/foundry/agents/publish-and-call/).

A practical pattern is to combine the approaches. Explore with integrated tools until you understand the analysis, then move the stable, expensive parts into a function and keep AIP Analyst for the interpretation and presentation around it.

## Related resources

- [Compute usage with AIP](/docs/foundry/aip/aip-compute-usage/)
- [Compute usage with Ontology queries](/docs/foundry/ontologies/query-compute-usage/)
- [Usage types](/docs/foundry/resource-management/usage-types/)
- [Supported LLMs](/docs/foundry/aip/supported-llms/)
