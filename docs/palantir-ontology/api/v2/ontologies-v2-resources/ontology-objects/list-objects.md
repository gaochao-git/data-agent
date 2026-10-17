# Palantir API Reference — List Objects

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-objects/list-objects/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objects/{objectType}**

`operationId`: `v2.listObjectsV2`

## Description

Lists the objects for the given Ontology and object type.

Note that this endpoint does not guarantee consistency. Changes to the data could result in missing or
repeated objects in the response pages.

For Object Storage V1 backed objects, this endpoint returns a maximum of 10,000 objects. After 10,000 objects have been returned and if more objects
are available, attempting to load another page will result in an `ObjectsExceededLimit` error being returned. There is no limit on Object Storage V2 backed objects.

Each page may be smaller or larger than the requested page size. However, it
is guaranteed that if there are more results available, at least one result will be present
in the response.

Note that null value properties will not be returned.


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
| `pageSize` | false | integer | The desired size of the page to be returned. Defaults to 1,000. See [page sizes](/docs/foundry/api/general/overview/paging/#page-sizes) for details. |
| `pageToken` | false | string | The page token indicates where to start paging. This should be omitted from the first page's request. To fetch the next page, clients should take the value from the `nextPageToken` field of the previous response and use it to populate the `pageToken` field of the next request. |
| `select` | false | list | The properties of the object type that should be included in the response. Omit this parameter to get all the properties. |
| `orderBy` | false | string | A command representing the list of properties to order by. Properties should be delimited by commas and prefixed by `p` or `properties`. The format expected format is `orderBy=properties.{property}:{sortDirection},properties.{property}:{sortDirection}...`  By default, the ordering for a property is ascending, and this can be explicitly specified by appending  `:asc` (for ascending) or `:desc` (for descending).  Example: use `orderBy=properties.lastName:asc` to order by a single property,  `orderBy=properties.lastName,properties.firstName,properties.age:desc` to order by multiple properties.  You may also use the shorthand `p` instead of `properties` such as `orderBy=p.lastName:asc`. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `excludeRid` | false | boolean | A flag to exclude the retrieval of the `__rid` property.  Setting this to true may improve performance of this endpoint for object types in OSV2. |
| `snapshot` | false | boolean | A flag to use snapshot consistency when paging. Setting this to true will give you a consistent view from before you start paging through the results, ensuring you do not get duplicate or missing items. Setting this to false will let new results enter as you page, but you may encounter duplicate or missing items. This defaults to false if not specified, which means you will always get the latest results. |
| `branch` | false | string | The Foundry branch to list objects from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `loadOntologyDefinedDerivedProperties` | false | boolean | A flag to load ontology-defined derived properties (OTDPs) in the response. Defaults to true. Only applies when no explicit property selection is provided; when specific properties are selected, this flag has no effect and the selected properties are always returned.  This feature is experimental and not yet generally available. |


## Response

`ListObjectsResponseV2`

```json
{
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZTogaHR0cHM6Ly93d3cucGFsYW50aXIuY29tL2NhcmVlcnMvP2xldmVyLXNvdXJjZSU1YiU1ZD1BUElEb2NzI29wZW4tcG9zaXRpb25z",
  "data": [
    {
      "__rid": "ri.phonograph2-objects.main.object.88a6fccb-f333-46d6-a07e-7725c5f18b61",
      "__primaryKey": 50030,
      "__apiName": "Employee",
      "id": 50030,
      "firstName": "John",
      "lastName": "Doe"
    },
    {
      "__rid": "ri.phonograph2-objects.main.object.dcd887d1-c757-4d7a-8619-71e6ec2c25ab",
      "__primaryKey": 20090,
      "__apiName": "Employee",
      "id": 20090,
      "firstName": "John",
      "lastName": "Haymore"
    }
  ]
}
```
