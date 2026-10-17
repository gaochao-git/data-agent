# Palantir API Reference — Get Outgoing Link Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/get-outgoing-link-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}/outgoingLinkTypes/{linkType}**

`operationId`: `v2.getOutgoingLinkTypeV2`

## Description

Get an outgoing link for an object type.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager** application. |
| `linkType` | true | string | The API name of the outgoing link. To find the API name for your link type, check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to get the outgoing link types for an object type from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |


## Response

`LinkTypeSideV2`

```json
{
  "apiName": "directReport",
  "objectTypeApiName": "Employee",
  "cardinality": "MANY"
}
```
