# Palantir API Reference — Get Object Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/get-object-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}**

`operationId`: `v2.getObjectTypeV2`

## Description

Gets a specific object type with the given API name.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to load the object type definition from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `includeDatasources` | false | boolean | When set to `true`, the `datasources` field on the returned object type is populated with the datasources backing it. Defaults to `false`. |


## Response

`ObjectTypeV2`

```json
{
  "apiName": "employee",
  "description": "A full-time or part-time employee of our firm",
  "displayName": "Employee",
  "status": "ACTIVE",
  "primaryKey": "employeeId",
  "properties": {
    "employeeId": {
      "dataType": {
        "type": "integer"
      },
      "rid": "ri.ontology.main.property.571d3d4d-150a-4dd4-b1a7-d16c1ed7d996"
    },
    "fullName": {
      "dataType": {
        "type": "string"
      },
      "rid": "ri.ontology.main.property.5721baa7-26d5-4ca8-b092-d47dcc724ab1"
    },
    "office": {
      "description": "The unique ID of the employee's primary assigned office",
      "dataType": {
        "type": "string"
      },
      "rid": "ri.ontology.main.property.554fa8c4-3b6e-4d3f-adef-acc9f0f54633"
    },
    "startDate": {
      "description": "The date the employee was hired (most recently, if they were re-hired)",
      "dataType": {
        "type": "date"
      },
      "rid": "ri.ontology.main.property.3b081417-fe68-4010-ade8-68b298116ed4"
    }
  },
  "rid": "ri.ontology.main.object-type.0381eda6-69bb-4cb7-8ba0-c6158e094a04"
}
```
