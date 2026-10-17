# Palantir API Reference — Get Linked Object

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/linked-objects/get-linked-object/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/links/{linkType}/{linkedObjectPrimaryKey}**

`operationId`: `v2.getLinkedObjectV2`

## Description

Get a specific linked object that originates from another object.

If there is no link between the two objects, `LinkedObjectNotFound` is thrown.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object from which the links originate. To look up the expected primary key for your object type, use the `Get object type` endpoint or the **Ontology Manager**. |
| `linkType` | true | string | The API name of the link that exists between the object and the requested objects. To find the API name for your link type, check the **Ontology Manager**. |
| `linkedObjectPrimaryKey` | true | string | The primary key of the requested linked object. To look up the expected primary key for your object type, use the `Get object type` endpoint (passing the linked object type) or the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `select` | false | list | The properties of the object type that should be included in the response. Omit this parameter to get all the properties. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `excludeRid` | false | boolean | A flag to exclude the retrieval of the `__rid` property.  Setting this to true may improve performance of this endpoint for object types in OSV2. |
| `branch` | false | string | The Foundry branch to load the object set for multiple object types. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |
| `loadOntologyDefinedDerivedProperties` | false | boolean | A flag to load ontology-defined derived properties (OTDPs) in the response. Defaults to true. Only applies when no explicit property selection is provided; when specific properties are selected, this flag has no effect and the selected properties are always returned.  This feature is experimental and not yet generally available. |


## Response

`OntologyObjectV2`

```json
{
  "__rid": "ri.phonograph2-objects.main.object.88a6fccb-f333-46d6-a07e-7725c5f18b61",
  "__primaryKey": 50030,
  "__apiName": "Employee",
  "id": 50030,
  "firstName": "John",
  "lastName": "Doe"
}
```
