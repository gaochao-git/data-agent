# Palantir API Reference — Get Outgoing Link Types By Object Type Rid Batch

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/get-outgoing-link-types-by-object-type-rid-batch/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/outgoingLinkTypes/getByRidBatch**

`operationId`: `v2.getOutgoingLinkTypesByObjectTypeRidBatch`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets outgoing link types for a batch of object types, identified by their RIDs.

For each requested object type, returns the list of outgoing link types visible to the
requesting token. Optionally, results can be filtered to only include specific link type RIDs.

Object types that don't exist or that the requesting token lacks permissions for are
silently omitted from the response.

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
| `branch` | false | string | The Foundry branch to load the outgoing link type definitions from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Request body

``

## Response

`GetOutgoingLinkTypesByObjectTypeRidBatchResponse`
