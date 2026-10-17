# Palantir Foundry Docs — /docs/foundry/palantir-mcp/available-tools

> 来源: https://www.palantir.com/docs/foundry/palantir-mcp/available-tools
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Available tools

Palantir MCP provides tools for working with Foundry resources, data, code, and documentation. The tables below list the tools and their availability in local development and Code Workspaces.

## Tool availability

The **Local development** column applies to Palantir MCP running on your machine. The **Code Workspaces** column applies to Palantir MCP running inside Foundry:

- **Yes:** The tool is enabled in that environment. In Code Workspaces, it is enabled for all repository and workspace types.
- **No:** The tool is not enabled in that environment. Tools with **Yes** for local development and **No** for Code Workspaces are local-only.
- **A named repository or workspace type:** The tool is enabled only for that type in Code Workspaces.
- **System prompt:** Relevant context is included automatically for the repository type, without a tool call. This does not mean the tool itself is callable. OSDK context applies to OSDK and OSDK widget repositories; ML documentation applies to model training and model adapter repositories.

The repository-specific labels mean the following:

- **Python transforms:** Python transforms repositories, including model training repositories
- **Python compute modules:** Python compute module repositories
- **Function and agent repositories:** Python functions, TypeScript v1 and v2 functions, and TypeScript pro-code agent repositories
- **Jupyter and RStudio:** Jupyter and RStudio workspaces only

For setup and permissions, see [installation](/docs/foundry/palantir-mcp/installation/) and [security](/docs/foundry/palantir-mcp/security/).

## Compass tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **list\_resources\_in\_foundry\_folder** | Returns resources available in a given folder or project RID, with pagination support. | Yes | Yes |
| **get\_project\_imports** | Returns datasets and other resources imported into a Foundry project, including external resources such as Spark profiles, with pagination support. | Yes | Yes |
| **list\_foundry\_namespaces** | Lists all Foundry namespaces that you can access. | Yes | No |
| **list\_foundry\_project\_templates** | Lists available project templates for a Foundry namespace. Templates define the initial structure and settings for new projects. | Yes | No |
| **create\_foundry\_project** | Creates a new Foundry project in a specified namespace using a specified template. | Yes | No |
| **search\_foundry\_projects** | Searches for Foundry projects by name or description. Returns matching projects with their RID, name, description, and path. | Yes | No |
| **search\_foundry\_resources** | Searches for datasets, folders, code repositories, and other resources by name, description, or path. Supports filtering by resource type, project, or namespace. Use `search_foundry_projects` to find projects. | Yes | No |
| **move\_foundry\_resources** | Moves resources into another folder within the same project. Does not support moving projects or moving resources between projects. | Yes | No |

## Dataset tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_foundry\_dataset\_schema** | Returns the definition of a Palantir dataset for a given dataset RID or dataset path, including schema information. | Yes | Yes |
| **run\_sql\_query\_on\_foundry\_dataset** | Returns the result of running a SQL query on a Palantir dataset on an optional branch. | Yes | Yes |
| **create\_and\_write\_to\_foundry\_dataset** | Creates a dataset in Foundry with data provided from a CSV file, useful for uploading local data or creating mock data. | Yes | Yes |
| **list\_dataset\_files** | Lists the files in a Foundry dataset for a given dataset RID or dataset path. Useful for exploring datasets without a schema. | Yes | Yes |
| **build\_datasets** | Builds datasets in Python transforms repositories. Commit and push changes before submitting a build. | Yes | Python transforms |
| **get\_build\_status** | Returns the build status of CI checks or dataset builds. Supports checking by build RID, tag, or branch. | Yes | Yes |
| **search\_dataset\_builds** | Searches for historic builds based on criteria such as dataset RIDs, build statuses, branches, and time ranges. | Yes | Yes |
| **get\_job\_status** | Retrieves detailed status information for a specific job by job RID, including error messages and source code information. | Yes | Yes |
| **get\_dataset\_stats** | Returns size and file statistics for a Foundry dataset, including total size, number of files, hidden files, and number of transactions. | Yes | Yes |

## Media tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_media\_set** | Returns a media set's configuration, including its primary media schema and additional allowed input formats. | Yes | Yes |
| **get\_media\_item\_metadata** | Returns structural metadata for a media item, such as its format, dimensions, duration, or page count, without returning the file content. | Yes | Yes |
| **list\_media\_items** | Lists media items with their RIDs, paths, MIME types, and retention-expiration timestamps, with pagination support. | Yes | Yes |
| **add\_media\_set\_allowed\_input\_schema** | Adds an allowed input format to a media set so it accepts uploads in that format. | Yes | No |
| **remove\_media\_set\_allowed\_input\_schema** | Removes an additional allowed input format from a media set. Existing media items are unaffected. | Yes | No |

## Data health tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **manage\_health\_check** | Creates, updates, or deletes health checks on Foundry datasets. | Yes | Yes |
| **get\_health\_check\_info** | Lists health checks, retrieves their configurations and latest reports, and discovers which datasets have checks configured. | Yes | Yes |
| **get\_available\_check\_types** | Lists available health check types and their supported validations. | Yes | Yes |
| **manage\_check\_group** | Creates or updates health check groups and manages check membership in groups and monitoring views. | Yes | Yes |
| **get\_check\_group\_info** | Lists accessible health check groups or retrieves a group's checks, notification schedule, and configuration. | Yes | Yes |
| **manage\_check\_state** | Pauses or resumes health checks, or retrieves their pause status. | Yes | Yes |

## Data Lineage tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_resource\_graph** | Returns the resource graph for data lineage visualization. | Yes | No |

## Ontology tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_foundry\_ontology\_rid** | Fetches the ontology RID for a given Foundry OSDK application or local repository. | Yes | System prompt |
| **search\_foundry\_ontology** | Searches the Ontology for relevant entities for a given query. | Yes | Yes |
| **search\_foundry\_functions** | Searches the function registry for functions matching a query. | Yes | Yes |
| **view\_foundry\_object\_type** | Displays an existing object type in the Ontology, including its properties, link types, and action types. | Yes | Yes |
| **create\_or\_update\_foundry\_object\_type** | Creates or updates object types in the Ontology on a provided branch. | Yes | Yes |
| **delete\_foundry\_object\_type** | Deletes an object type from the Ontology on a provided branch. | Yes | Yes |
| **view\_foundry\_link\_type** | Displays an existing link type in the Ontology. | Yes | Yes |
| **create\_or\_update\_foundry\_link\_type** | Creates or updates link types in the Ontology on a provided branch. | Yes | Yes |
| **delete\_foundry\_link\_type** | Deletes a link type from the Ontology on a provided branch. | Yes | Yes |
| **view\_foundry\_action\_type** | Displays an existing action type in the Ontology, including its parameters, logic rules, and dependent object types. | Yes | Yes |
| **create\_or\_update\_foundry\_action\_type** | Creates or updates action types in the Ontology on a provided branch. Supports object modifications, deletions, and link operations. | Yes | Yes |
| **delete\_foundry\_action\_type** | Deletes an action type from the Ontology on a provided branch. | Yes | Yes |

## Object set tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **query\_ontology\_objects** | Queries ontology objects based on specified criteria with pagination support. | Yes | No |
| **aggregate\_ontology\_objects** | Performs aggregation functions on object fields, supporting grouping, filtering, and multiple aggregation types. | Yes | No |
| **semantic\_search** | Finds ontology objects similar to a natural-language query using a vector embedding property and its configured embedding model. Returns matches ordered by similarity. | Yes | No |

## OSDK tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_ontology\_sdk\_context** | Fetches curated documentation about the Ontology SDK for your OSDK repository. | Yes | System prompt |
| **get\_ontology\_sdk\_examples** | Retrieves code examples for using the Ontology SDK, filtered by SDK version and topic. | Yes | Yes |
| **create\_or\_update\_ontology\_sdk\_version** | Creates or updates an OSDK by publishing a new version with the specified ontology entities. Supports Developer Console applications and standalone SDK packages. | Yes | Yes |
| **install\_sdk\_package** | Checks SDK package generation status and provides installation instructions when ready. Supports Developer Console applications and standalone SDK packages. | Yes | Yes |

## Platform SDK tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **list\_platform\_sdk\_apis** | Lists available Platform SDK APIs for a given Foundry product. | Yes | Yes |
| **get\_platform\_sdk\_api\_reference** | Gets API reference documentation and code examples for Platform SDK API endpoints. | Yes | Yes |

## Code Repository tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_repository\_context** | Fetches curated context about the current Foundry code repository, including project and ontology information. | Yes | Yes; system prompt |
| **create\_python\_transforms\_code\_repository** | Creates a new Python transforms code repository. | Yes | No |
| **clone\_code\_repository\_locally** | Clones an existing Foundry code repository to a local directory. | Yes | No |
| **create\_code\_repository\_pull\_request** | Creates a pull request in a Foundry code repository. | Yes | Yes |
| **list\_code\_repository\_pull\_requests** | Lists pull requests for a Foundry code repository with filtering by status, branch, author, and title. | Yes | Yes |
| **get\_code\_repository\_pull\_request** | Gets pull request details, including review status, CI build status, file changes, and comments. | Yes | Yes |
| **create\_code\_repository\_pull\_request\_comment** | Creates a comment on a Foundry code repository pull request. Supports general comments and inline comments anchored to a specific file and line. | Yes | Yes |
| **publish\_function** | Prepares a release of a function or pro-code agent repository by selecting a semantic version and returning Git commands to create and push the release tag. Requires a clean working tree; publication completes through CI after the commands run. | Yes | Function and agent repositories |

## Code Workspaces tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_foundry\_environment\_declared\_data\_scope** | Lists dataset, table, and restricted-view aliases declared for the current workspace, with import snippets where supported. Does not enumerate OSDK data scope. | No | Jupyter and RStudio |

## Global Branching tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **create\_global\_branch** | Creates a new global branch with a specified display name and description. | Yes | Yes |
| **view\_global\_branch** | Displays an existing global branch, including branched resources and ontology modifications. | Yes | Yes |
| **close\_global\_branch** | Closes an existing global branch. | Yes | Yes |
| **create\_global\_proposal** | Creates a proposal for changes on a global branch. | Yes | Yes |
| **view\_global\_proposal** | Displays an existing proposal and contextual information based on its status. | Yes | Yes |
| **close\_global\_proposal** | Closes an existing proposal without merging it. | Yes | Yes |

## Developer Console tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **connect\_to\_dev\_console\_app** | Connects a non-Foundry Git repository to a Developer Console application. | Yes | No |
| **convert\_to\_osdk\_react** | Converts an existing OSDK application to use `@osdk/react` with `OsdkProvider2` and optionally `@osdk/react-components`. | Yes | Yes |
| **view\_osdk\_definition** | Fetches a high-level overview of the OSDK definition, including object types, link types, action types, and functions. | Yes | Yes |

## Compute module tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_compute\_modules\_documentation** | Fetches curated documentation about compute modules and the `compute_modules` Python SDK. | Yes | Python compute modules |
| **get\_compute\_modules\_info** | Gets status and configuration for a compute module, including runtime status, scaling configuration, and runtime parameters. | Yes | No |
| **get\_compute\_modules\_logs** | Retrieves the latest logs for a compute module by build job RID, with pagination support. | Yes | No |
| **manage\_compute\_modules** | Manages a compute module lifecycle: start, stop, or configure development mode for automatic upgrades. | Yes | No |
| **execute\_compute\_modules\_function** | Executes a compute module function synchronously. Only works with compute modules running in `FUNCTION` mode. | Yes | No |

## Data Connection tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **create\_foundry\_rest\_api\_data\_source** | Creates a REST API data source connection in Foundry for integrating with external systems. | Yes | No |
| **create\_foundry\_rest\_api\_data\_source\_webhook** | Creates a webhook for a REST API data source in Foundry. | Yes | No |
| **update\_foundry\_rest\_api\_data\_source\_webhook** | Updates an existing webhook in Foundry by publishing a new version with an updated specification. | Yes | No |
| **view\_foundry\_rest\_api\_data\_source\_webhook** | Fetches the latest version of an existing webhook in Foundry, including its metadata, specification, inputs, calls, and outputs. | Yes | No |
| **get\_or\_create\_network\_egress\_policy** | Gets or creates a network egress policy in Foundry. Useful when creating REST API data sources and webhooks. | Yes | No |

## Documentation search tools

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **get\_python\_transforms\_documentation** | Fetches curated documentation about Python transforms and the `transforms.api` SDK. | Yes | Python transforms; system prompt |
| **get\_typescript\_v1\_functions\_documentation** | Retrieves documentation and code snippets for TypeScript v1 function repositories. | Yes | System prompt |
| **get\_typescript\_v2\_functions\_documentation** | Retrieves documentation and code snippets for TypeScript v2 function repositories. | Yes | System prompt |
| **get\_custom\_widget\_documentation** | Gets documentation for custom widgets, including React widgets for Workshop and Slate. | Yes | System prompt |
| **get\_ml\_documentation** | Fetches ML documentation for Python transforms, covering model training, MLOps, and framework-specific patterns. | Yes | Python transforms; system prompt |
| **get\_spark\_profile\_documentation** | Fetches documentation on Spark profile configuration for Python transforms, including guidance for selecting executor memory, number of executors, and driver settings. | Yes | Python transforms |
| **get\_osdk\_react\_components\_documentation** | Gets documentation for the `@osdk/react-components` library, including `ObjectTable` and other pre-built components. | Yes | Yes |
| **load\_foundry\_documentation\_page** | Loads a full Palantir Foundry documentation page by its path and returns the complete page content in Markdown format. | Yes | Yes |
| **get\_documentation\_summaries** | Retrieves a curated documentation summary bundle for a specific Foundry topic area, covering key concepts, patterns, and reference material. | Yes | Yes |
| **search\_foundry\_documentation** | Searches Palantir Foundry platform documentation indices for relevant context snippets for a given query. Requires AIP. | Yes | Yes |

## Tool discovery

The `search_tools` tool is enabled by default in both environments. With [tool search](/docs/foundry/palantir-mcp/tool-search/), only `search_tools` is initially visible. It reveals tools available in your session and returns their full descriptions, input schemas, and other available metadata. Permitted tools remain callable on the server before discovery, but some clients require a tool-list refresh to call them. Tool search preserves environment and configuration restrictions; it does not make local-only tools available in Code Workspaces. You can [disable tool search](/docs/foundry/palantir-mcp/tool-search/#disable-tool-search) to load the full permitted catalog at startup.

| Tool name | Description | Local development | Code Workspaces |
| --- | --- | --- | --- |
| **search\_tools** | Finds and reveals tools for a specific task using a natural-language query. | Enabled by default | Enabled by default |
