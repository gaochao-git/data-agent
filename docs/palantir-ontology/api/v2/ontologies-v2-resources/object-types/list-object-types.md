# Palantir API Reference — List Object Types

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/list-object-types/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objectTypes**

`operationId`: `v2.listObjectTypesV2`

## Description

Lists the object types for the given Ontology.

Each page may be smaller or larger than the requested page size. However, it is guaranteed that if there are
more results available, at least one result will be present in the
response.

Note: the `aliases` field is not populated on this endpoint and will always be empty. To retrieve object type
aliases, use the get-by-RID read paths (e.g. `getObjectTypeV2`).


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to list the object types from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `pageSize` | false | integer | The desired size of the page to be returned. Defaults to 500. See [page sizes](/docs/foundry/api/general/overview/paging/#page-sizes) for details. |
| `pageToken` | false | string | The page token indicates where to start paging. This should be omitted from the first page's request. To fetch the next page, clients should take the value from the `nextPageToken` field of the previous response and use it to populate the `pageToken` field of the next request. |
| `includeDatasources` | false | boolean | When set to `true`, the `datasources` field on each returned object type is populated with the datasources backing it. Defaults to `false`. |


## Response

`ListObjectTypesV2Response`

```json
{
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z",
  "data": [
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
        },
        "rid": "ri.ontology.main.object-type.401ac022-89eb-4591-8b7e-0a912b9efb44"
      }
    },
    {
      "apiName": "office",
      "description": "A physical location (not including rented co-working spaces)",
      "displayName": "Office",
      "status": "ACTIVE",
      "primaryKey": "officeId",
      "properties": {
        "officeId": {
          "dataType": {
            "type": "string"
          }
        },
        "address": {
          "description": "The office's physical address (not necessarily shipping address)",
          "dataType": {
            "type": "string"
          }
        },
        "capacity": {
          "description": "The maximum seated-at-desk capacity of the office (maximum fire-safe capacity may be higher)",
          "dataType": {
            "type": "integer"
          }
        },
        "rid": "ri.ontology.main.object-type.9a0e4409-9b50-499f-a637-a3b8334060d9"
      }
    }
  ]
}
```
