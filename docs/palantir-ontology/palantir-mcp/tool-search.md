# Palantir Foundry Docs — /docs/foundry/palantir-mcp/tool-search

> 来源: https://www.palantir.com/docs/foundry/palantir-mcp/tool-search
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Tool search

Palantir MCP enables tool search by default. The server initially shows only the `search_tools` tool in your agent's tool list. Your agent discovers additional tools as it needs them, so fewer tool definitions load into your context, which reduces token usage.

## How tool search works

The `search_tools` description includes capability categories and examples for the tools available in your session. From there:

1. When you prompt the agent, the agent calls `search_tools` with a short, specific description of the capability it needs, such as `view an action type` or `run a SQL query`.
2. `search_tools` ranks the session's catalog locally by tool name, category, and curated keywords. Ranking requires no extra model call or network request.
3. The search returns matching tool names, categories, full descriptions, input schemas, and other available tool metadata. Before returning, it adds the matching tools to your agent's tool list and sends a `tools/list_changed` notification if the list changed.
4. The agent uses the returned schema to call a matching tool.

This keeps your context small. Instead of loading dozens of tool definitions up front, the agent loads only the tools relevant to the task.

Discovered tools remain in your tool list for the rest of the session. Initializing a new session resets the visible tool list to only `search_tools`.

Tool search controls visibility, not permission to execute a tool. All tools permitted by your environment and configuration remain callable on the server, even before discovery or a tool-list refresh. Environment and data-access restrictions, explicitly disabled tools, input validation, and permission checks still apply.

**Client compatibility:** Some MCP clients only call tools in their cached tool list and do not refresh it when Palantir MCP sends a `tools/list_changed` notification. If your client works this way, [disable tool search](#disable-tool-search) to load the full permitted catalog at startup. Clients that support dynamic discovery refresh their tool list automatically.

## Tool search settings

No additional configuration is needed to enable tool search. Existing configurations that pass `--tool-search` or set `PALANTIR_MCP_TOOL_SEARCH=true` remain supported. Command-line flags take precedence over the environment variable.

### Disable tool search

To load the full permitted tool catalog at startup, use either option:

- Pass the `--no-tool-search` argument to the server.
- Set the environment variable `PALANTIR_MCP_TOOL_SEARCH=false`.

Explicitly disabling `search_tools` with `--disable-tools search_tools` also loads the full permitted catalog at startup. Disabling tool search does not change environment, data-access, or permission restrictions.

In the examples below, replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).

#### Claude Code

To disable tool search when you add Palantir MCP with `claude mcp add`, pass the `--no-tool-search` argument. The argument must go after the `--` separator, as an argument to `palantir-mcp`.

```
Copied!

1
2
3
4
claude mcp add palantir-mcp \
    --scope project \
    -e FOUNDRY_TOKEN=<token> \
    -- npx "-y" "palantir-mcp" "--no-tool-search" "--foundry-api-url" "https://<your-foundry-hostname>"
```

Alternatively, set the environment variable:

```
Copied!

1
2
3
4
5
claude mcp add palantir-mcp \
    --scope project \
    -e FOUNDRY_TOKEN=<token> \
    -e PALANTIR_MCP_TOOL_SEARCH=false \
    -- npx "-y" "palantir-mcp" "--foundry-api-url" "https://<your-foundry-hostname>"
```

#### Agents

Tool search is enabled by default in a pro-code agent's Palantir MCP server. To disable it in a custom server configuration, add the `--no-tool-search` argument:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
export const PALANTIR_MCP_CONFIGURATION: McpStdioServerConfig = {
  type: "stdio",
  command: "palantir-mcp",
  args: ["start", "--no-tool-search", "code-workspaces"],
  env: {
    FOUNDRY_PROXY_URL: `https://${process.env.FOUNDRY_PROXY_URL!}/`,
    FOUNDRY_SERVICE_DISCOVERY_V2: process.env.FOUNDRY_SERVICE_DISCOVERY_V2!,
    FOUNDRY_EXTERNAL_HOST: "https://<your-foundry-hostname>",
    FOUNDRY_PROXY_TOKEN: process.env.FOUNDRY_PROXY_TOKEN!,
    FOUNDRY_SCOPED_TOKEN: process.env.FOUNDRY_SCOPED_TOKEN!,
    MAESTRO_REPO_RID: process.env.MAESTRO_REPO_RID!,
    NODE_EXTRA_CA_CERTS: process.env.NODE_EXTRA_CA_CERTS!,
  },
};
```

Alternatively, add `PALANTIR_MCP_TOOL_SEARCH: "false"` to the `env` block instead of passing the `--no-tool-search` argument. To avoid conflicts during repository upgrades, add custom MCP configurations in their own files, as described in the [agent template guidance](/docs/foundry/agents/agent-templates/#project-structure).
