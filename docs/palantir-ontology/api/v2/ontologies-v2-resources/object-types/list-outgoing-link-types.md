# Palantir API Reference — List Outgoing Link Types

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/list-outgoing-link-types/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}/outgoingLinkTypes**

`operationId`: `v2.listOutgoingLinkTypesV2`

## Description

List the outgoing links for an object type.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager** application. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to load the outgoing link types from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `pageSize` | false | integer | The desired size of the page to be returned. |
| `pageToken` | false | string | The page token indicates where to start paging. This should be omitted from the first page's request. To fetch the next page, clients should take the value from the `nextPageToken` field of the previous response and use it to populate the `pageToken` field of the next request. |


## Response

`ListOutgoingLinkTypesResponseV2`

```json
{
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z",
  "data": [
    {
      "apiName": "originAirport",
      "objectTypeApiName": "Airport",
      "cardinality": "ONE",
      "foreignKeyPropertyApiName": "originAirportId"
    }
  ]
}
```
