# Palantir API Reference — List Interface Types

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-interfaces/list-interface-types/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/interfaceTypes**

`operationId`: `v2.listInterfaceTypes`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Lists the interface types for the given Ontology.

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
| `branch` | false | string | The Foundry branch to list the interface types from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `pageSize` | false | integer | The desired size of the page to be returned. Defaults to 500. See [page sizes](/docs/foundry/api/general/overview/paging/#page-sizes) for details. |
| `pageToken` | false | string | The page token indicates where to start paging. This should be omitted from the first page's request. To fetch the next page, clients should take the value from the `nextPageToken` field of the previous response and use it to populate the `pageToken` field of the next request. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Response

`ListInterfaceTypesResponse`

```json
{
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z",
  "data": [
    {
      "apiName": "Athlete",
      "displayName": "Athlete",
      "description": "Good at sportsball",
      "properties": {
        "name": {
          "rid": "com.palantir.property.d1abdbfe-0ce2-4fff-b0af-af21002c314b",
          "apiName": "name",
          "displayName": "Name",
          "dataType": "string"
        }
      },
      "extendsInterfaces": [
        "Human"
      ],
      "rid": "ri.ontology.main.interface.bea1af8c-7d5c-4ec9-b845-8eeed6d77482"
    }
  ]
}
```
