# Palantir API Reference — Load Object Set

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-object-sets/load-object-set/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objectSets/loadObjects**

`operationId`: `v2.loadObjectSetV2`

## Description

Load the ontology objects present in the `ObjectSet` from the provided object set definition.

For Object Storage V1 backed objects, this endpoint returns a maximum of 10,000 objects. After 10,000 objects have been returned and if more objects
are available, attempting to load another page will result in an `ObjectsExceededLimit` error being returned. There is no limit on Object Storage V2 backed objects.

Note that null value properties will not be returned.

Vector properties will not be returned unless included in the `select` parameter.

The `pageSize` parameter is a maximum. A page may contain fewer objects than requested if the
objects in the page are large enough to reach an internal memory limit; this does not indicate
that there are no more results. As long as the response contains a `nextPageToken`, the remaining
objects can be retrieved by requesting subsequent pages.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The package version of the generated SDK. |
| `branch` | false | string | The Foundry branch to load the object set from. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |
| `transactionId` | false | string | The ID of an Ontology transaction to read from. Transactions are an experimental feature and all workflows may not be supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to load the object set from. |
| `executeInMemoryOnly` | false | boolean | If true, the request fails with an error when it cannot be computed in-memory. Use this to opt into fast failure on requests that would otherwise require heavier computation.  Defaults to false. |


## Request body

``

```json
{
  "objectSet": {
    "type": "base",
    "objectType": "Employee"
  },
  "pageSize": 10000,
  "pageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z"
}
```


## Response

`LoadObjectSetResponseV2`

```json
{
  "data": [
    {
      "__rid": "ri.phonograph2-objects.main.object.5b5dbc28-7f05-4e83-a33a-1e5b851",
      "__primaryKey": 50030,
      "__apiName": "Employee",
      "employeeId": 50030,
      "firstName": "John",
      "lastName": "Smith",
      "age": 21
    },
    {
      "__rid": "ri.phonograph2-objects.main.object.88a6fccb-f333-46d6-a07e-7725c5f18b61",
      "__primaryKey": 20090,
      "__apiName": "Employee",
      "employeeId": 20090,
      "firstName": "John",
      "lastName": "Haymore",
      "age": 27
    }
  ]
}
```
