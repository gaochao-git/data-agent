# Palantir API Reference — List Action Types

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/action-types/list-action-types/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/actionTypes**

`operationId`: `v2.listActionTypesV2`

## Description

Lists the action types for the given Ontology.

Each page may be smaller than the requested page size. However, it is guaranteed that if there are more
results available, at least one result will be present in the response.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to list the action types from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `pageSize` | false | integer | The desired size of the page to be returned. Defaults to 500. See [page sizes](/docs/foundry/api/general/overview/paging/#page-sizes) for details. |
| `pageToken` | false | string | The page token indicates where to start paging. This should be omitted from the first page's request. To fetch the next page, clients should take the value from the `nextPageToken` field of the previous response and use it to populate the `pageToken` field of the next request. |


## Response

`ListActionTypesResponseV2`

```json
{
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z",
  "data": [
    {
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
    },
    {
      "apiName": "move-office",
      "description": "Update an office's physical location",
      "parameters": {
        "officeId": {
          "dataType": {
            "type": "string"
          }
        },
        "newAddress": {
          "description": "The office's new physical address (not necessarily shipping address)",
          "dataType": {
            "type": "string"
          }
        },
        "newCapacity": {
          "description": "The maximum seated-at-desk capacity of the new office (maximum fire-safe capacity may be higher)",
          "dataType": {
            "type": "integer"
          }
        }
      },
      "rid": "ri.ontology.main.action-type.9f84017d-cf17-4fa8-84c3-8e01e5d594f2"
    }
  ]
}
```
