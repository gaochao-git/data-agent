# Palantir API Reference — Apply Action

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/actions/apply-action/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/actions/{action}/apply**

`operationId`: `v2.applyActionV2`

## Description

Applies an action using the given parameters. 

Changes to objects or links stored in Object Storage V1 are eventually consistent and may take some time to be visible.
Edits to objects or links in Object Storage V2 will be visible immediately after the action completes.

Note that a 200 HTTP status code only indicates that the request was received and processed by the server. 
See the validation result in the response body to determine if the action was applied successfully.

Note that [parameter default values](/docs/foundry/action-types/parameters-default-value/) are not currently supported by
this endpoint.


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
| `transactionId` | false | string | The ID of an Ontology transaction to apply the action against. Transactions are an experimental feature and all workflows may not be supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to apply the action against. |
| `branch` | false | string | The Foundry branch to apply the action against. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |


## Request body

``

```json
{
  "parameters": {
    "id": 80060,
    "newName": "Anna Smith-Doe"
  }
}
```


## Response

`SyncApplyActionResponseV2`

```json
{
  "operationId": "ri.actions.main.action.c61d9ab5-2919-4127-a0a1-ac64c0ce6367",
  "validation": {
    "result": "VALID"
  },
  "parameters": {
    "id": {
      "evaluatedConstraints": [],
      "result": "VALID",
      "required": true
    },
    "newName": {
      "evaluatedConstraints": [],
      "result": "VALID",
      "required": true
    }
  }
}
```
