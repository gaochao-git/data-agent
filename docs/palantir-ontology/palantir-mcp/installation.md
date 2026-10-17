# Palantir Foundry Docs — /docs/foundry/palantir-mcp/installation

> 来源: https://www.palantir.com/docs/foundry/palantir-mcp/installation
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Installation

Open the **MCP Hub** application in Foundry and select the **Palantir MCP** tab for the recommended setup instructions.

![The MCP Hub welcome page displays Palantir MCP installation instructions.](/docs/resources/foundry/palantir-mcp/palantir-mcp-hub-welcome.png)

The Palantir model context protocol (MCP) enables external AI systems to read data and metadata from Foundry and interact via AI-friendly API endpoints using the configured user token permissions. Once data is accessed by an external system through Palantir MCP, the governance of its use shifts from Palantir to the external system. Data security will then depend on your relationship with that external system. Using the Palantir MCP does not imply that a Palantir AI model will be used; the AI model provider is determined by the external system you connect to, such as Microsoft for GitHub Copilot, or Cursor.

Use the instructions below as a fallback to set up Palantir MCP with a supported IDE. Alternatively, use the [AI development tools](/docs/foundry/vs-code/ai-development-tools/) available in a VS Code workspace to access Palantir MCP.

[Tool search](/docs/foundry/palantir-mcp/tool-search/) is enabled by default, so your agent discovers tools as needed. If your MCP client does not support dynamic discovery, it may only use the initial tool list. In this case, add `--no-tool-search` to the server arguments in your configuration to load the full permitted catalog at startup. You can also [disable tool search with an environment variable](/docs/foundry/palantir-mcp/tool-search/#disable-tool-search).

## 1. Enable Palantir MCP in Control Panel

Platform administrators must first enable Palantir MCP for use through Control Panel. Access can be configured for a specific subset of users or groups. Palantir MCP is in Control Panel under **Code Repositories**.

![The Palantir MCP in Control Panel](/docs/resources/foundry/palantir-mcp/palantir-mcp-in-control-panel.png)

## 2. Configure your IDE to use Palantir MCP

If you are working within a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. In this case, you do not need to generate or explicitly configure a user token. When using your Foundry Git token:

- For Claude Code, omit the `export FOUNDRY_TOKEN` line and the `-e FOUNDRY_TOKEN` argument.
- For the other IDEs, omit the `env` block that sets `FOUNDRY_TOKEN`.
- For GitHub Copilot in VS Code, also omit the `inputs` array that prompts for a user token.

See instructions for:

- [Claude Code](#claude-code)
- [Cline](#cline)
- [Continue](#continue)
- [Cursor](#cursor)
- [VS Code Copilot](#github-copilot-in-vs-code)
- [Windsurf](#windsurf)

### Claude Code

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir, then replace `<token>` in the commands below with your token.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit both the `export FOUNDRY_TOKEN` line and the `-e FOUNDRY_TOKEN` argument below.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`). Do not prefix with "https://".
3. Review the [Claude Code documentation ↗](https://code.claude.com/docs/en/mcp) to install Palantir MCP in your Claude Code CLI, particularly the documentation around scoping where MCP should be installed. We recommend installing the MCP at the [project scope ↗](https://code.claude.com/docs/en/mcp#project-scope) level so that the MCP server configuration is scoped to the current project.
4. Run the following commands in your terminal:

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
export FOUNDRY_HOST="<your-foundry-hostname>"
# Only when not working in a Foundry repository:
export FOUNDRY_TOKEN=<token>

# When working in a Foundry repository, omit the -e FOUNDRY_TOKEN argument.
claude mcp add palantir-mcp \
    --scope project \
    -e FOUNDRY_TOKEN='${FOUNDRY_TOKEN}' \
    -- npx "-y" "palantir-mcp" "--foundry-api-url" "https://$FOUNDRY_HOST"
```

### Cline

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir, then replace `<token>` in the configuration below with your token.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit the `env` block that sets `FOUNDRY_TOKEN`.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).
3. Review the [Cline documentation ↗](https://docs.cline.bot/mcp/configuring-mcp-servers) to install Palantir MCP into your Cline extension with the configuration below.

json

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
15
16
17
{
    "mcpServers": {
        "palantir-mcp": {
            "command": "npx",
            "args": [
                "-y",
                "palantir-mcp",
                "--foundry-api-url",
                "https://<your-foundry-hostname>"
            ],
            "env": {
                "FOUNDRY_TOKEN": "<token>"
            },
            "disabled": false
        }
    }
}
```

### Continue

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir, then replace `<token>` in the configuration below with your token.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit the `env` block that sets `FOUNDRY_TOKEN`.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).
3. Review the [Continue documentation ↗](https://docs.continue.dev/customize/deep-dives/mcp) to install Palantir MCP into your Continue extension with the configuration below.

yaml

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
mcpServers:
  - name: palantir-mcp
    command: npx
    args:
      - "-y"
      - "palantir-mcp"
      - "--no-tool-search"
      - "--foundry-api-url"
      - "https://<your-foundry-hostname>"
    env:
        FOUNDRY_TOKEN: "<token>"
```

### Cursor

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir, then replace `<token>` in the configuration below with your token.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit the `env` block that sets `FOUNDRY_TOKEN` in either configuration below.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).
3. Review the [Cursor documentation ↗](https://docs.cursor.com/context/model-context-protocol) to install Palantir MCP into your Cursor IDE with the configuration below.

For macOS:

json

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
15
16
17
{
    "mcpServers": {
        "palantir-mcp": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "palantir-mcp",
                "--foundry-api-url",
                "https://<your-foundry-hostname>"
            ],
            "env": {
                "FOUNDRY_TOKEN": "<token>"
            }
        }
    }
}
```

For Windows:

json

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
15
16
17
{
    "mcpServers": {
        "palantir-mcp": {
            "type": "stdio",
            "command": "cmd /k npx",
            "args": [
                "-y",
                "palantir-mcp",
                "--foundry-api-url",
                "https://<your-foundry-hostname>"
            ],
            "env": {
                "FOUNDRY_TOKEN": "<token>"
            }
        }
    }
}
```

### GitHub Copilot in VS Code

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir and enter it when VS Code prompts you. The configuration below has no `<token>` placeholder to replace: `FOUNDRY_TOKEN` takes its value from the `foundry-token` input declared in the `inputs` array.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit both the `env` block that sets `FOUNDRY_TOKEN` and the `inputs` array that prompts for a user token.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).
3. Review the [VS Code documentation ↗](https://code.visualstudio.com/docs/copilot/chat/mcp-servers) to install Palantir MCP into your VS Code IDE with the configuration below.

json

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
15
16
17
18
19
20
21
22
23
24
25
"mcp": {
    "inputs": [
        {
            "type": "promptString",
            "id": "foundry-token",
            "description": "Foundry user token",
            "password": true
        }
    ],
    "servers": {
        "palantir-mcp": {
            "type": "stdio",
            "command": "npx",
            "args": [
                "-y",
                "palantir-mcp",
                "--foundry-api-url",
                "https://<your-foundry-hostname>"
            ],
            "env": {
                "FOUNDRY_TOKEN": "${input:foundry-token}"
            }
        }
    }
}
```

### Windsurf

1. If you are not working in a Foundry repository, [generate a user token](/docs/foundry/platform-security-third-party/user-generated-tokens/) from Palantir, then replace `<token>` in the configuration below with your token.

   If you are working in a Foundry repository, Palantir MCP can use your Foundry Git token to bootstrap authentication. Skip token generation and omit the `env` block that sets `FOUNDRY_TOKEN`.
2. Replace `<your-foundry-hostname>` with your Foundry hostname (for example, `mycompany.palantirfoundry.com`).
3. Review the [Windsurf documentation ↗](https://docs.windsurf.com/windsurf/cascade/mcp) to install Palantir MCP into your Windsurf IDE with the configuration below.

json

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
15
16
{
    "mcpServers": {
        "palantir-mcp": {
            "command": "npx",
            "args": [
                "-y",
                "palantir-mcp",
                "--foundry-api-url",
                "https://<your-foundry-hostname>"
            ],
            "env": {
                "FOUNDRY_TOKEN": "<token>"
            }
        }
    }
}
```
