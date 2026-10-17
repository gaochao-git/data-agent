# Palantir API Reference — Get Action Type By Rid Batch

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/action-types/get-action-type-by-rid-batch/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/actionTypes/getByRidBatch**

`operationId`: `v2.getActionTypeByRidBatch`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets a list of action types by RID in bulk.

Action types are filtered from the response if they don't exist or the requesting token lacks the required
permissions.

The maximum batch size for this endpoint is 100.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to load the action type definitions from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Request body

``

## Response

`GetActionTypeByRidBatchResponse`
