# Palantir API Reference — Get Object Type Edits History

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/get-object-type-edits-history/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objectTypes/{objectType}/editsHistory**

`operationId`: `v2.getObjectTypeEditsHistory`

## Description

Returns the history of edits (additions, modifications, deletions) for objects of a
specific object type. This endpoint provides visibility into all actions that have
modified objects of this type.

The edits are returned in reverse chronological order (most recent first) by default. 

Note that filters are ignored for OSv1 object types.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The ontology RID or API name |
| `objectType` | true | string | The API name of the object type |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch from which we will get edits history. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to get edits history from. |


## Request body

``

## Response

`ObjectTypeEditsHistoryResponse`
