# Palantir API Reference — Apply Action Batch

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/actions/apply-action-batch/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/actions/{action}/applyBatch**

`operationId`: `v2.applyActionBatchV2`

## Description

Applies multiple actions (of the same Action Type) using the given parameters.

Changes to objects or links stored in Object Storage V1 are eventually consistent and may take some time to be visible.
Edits to objects or links in Object Storage V2 will be visible immediately after the action completes.

Up to 20 actions may be applied in one call. Actions that only modify objects in Object Storage v2 and do not
call Functions may receive a higher limit.

Note that [notifications](/docs/foundry/action-types/notifications/) are not currently supported by this endpoint.


Third-party applications using this endpoint via OAuth2 must request the following operation scopes: `api:ontologies-read api:ontologies-write`.

**OAuth scopes**: `api:ontologies-read`, `api:ontologies-write`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `action` | true | string | The API name of the action to apply. To find the API name for your action, use the **List action types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to apply the actions against. |
| `branch` | false | string | The Foundry branch to apply the action against. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |


## Request body

``

```json
{
  "requests": [
    {
      "parameters": {
        "id": 80060,
        "newName": "Anna Smith-Doe"
      }
    },
    {
      "parameters": {
        "id": 80061,
        "newName": "Joe Bloggs"
      }
    }
  ]
}
```


## Response

`BatchApplyActionResponseV2`

```json
{}
```
