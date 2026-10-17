# Palantir API Reference — Get Action Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/action-types/get-action-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/actionTypes/{actionType}**

`operationId`: `v2.getActionTypeV2`

## Description

Gets a specific action type with the given API name.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `actionType` | true | string | The name of the action type in the API. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to load the action type definition from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |


## Response

`ActionTypeV2`

```json
{
  "data": {
    "apiName": "promote-employee",
    "description": "Update an employee's title and compensation",
    "parameters": {
      "employeeId": {
        "dataType": {
          "type": "integer"
        }
      },
      "newTitle": {
        "dataType": {
          "type": "string"
        }
      },
      "newCompensation": {
        "dataType": {
          "type": "double"
        }
      }
    },
    "rid": "ri.ontology.main.action-type.7ed72754-7491-428a-bb18-4d7296eb2167"
  }
}
```
