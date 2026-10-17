# Palantir API Reference — Search Objects

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-objects/search-objects/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objects/{objectType}/search**

`operationId`: `v2.searchObjectsV2`

## Description

Search for objects in the specified ontology and object type. The request body is used
to filter objects based on the specified query. The supported queries are:

| Query type                              | Description                                                                                                       | Supported Types                 |
|-----------------------------------------|-------------------------------------------------------------------------------------------------------------------|---------------------------------|
| lt                                      | The provided property is less than the provided value.                                                            | number, string, date, timestamp |
| gt                                      | The provided property is greater than the provided value.                                                         | number, string, date, timestamp |
| lte                                     | The provided property is less than or equal to the provided value.                                                | number, string, date, timestamp |
| gte                                     | The provided property is greater than or equal to the provided value.                                             | number, string, date, timestamp |
| eq                                      | The provided property is exactly equal to the provided value.                                                     | number, string, date, timestamp |
| isNull                                  | The provided property is (or is not) null.                                                                        | all                             |
| contains                                | The provided property contains the provided value.                                                                | array                           |
| not                                     | The sub-query does not match.                                                                                     | N/A (applied on a query)        |
| and                                     | All the sub-queries match.                                                                                        | N/A (applied on queries)        |
| or                                      | At least one of the sub-queries match.                                                                            | N/A (applied on queries)        |
| containsAllTermsInOrderPrefixLastTerm   | The provided property contains all the terms provided in order. The last term can be a partial prefix match.      | string                          |
| containsAllTermsInOrder                 | The provided property contains the provided term as a substring.                                                  | string                          |
| containsAnyTerm                         | The provided property contains at least one of the terms separated by whitespace.                                 | string                          |
| containsAllTerms                        | The provided property contains all the terms separated by whitespace.                                             | string                          |
| startsWith                              | Deprecated alias for containsAllTermsInOrderPrefixLastTerm.                                                       | string                          |

Queries can be at most three levels deep. By default, terms are separated by whitespace or punctuation (`?!,:;-[](){}'"~`). Periods (`.`) on their own are ignored.
Partial terms are not matched by terms filters except where explicitly noted.

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
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to search objects from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `executeInMemoryOnly` | false | boolean | If true, the request fails with an error when it cannot be computed in-memory. Use this to opt into fast failure on requests that would otherwise require heavier computation.  Defaults to false. |


## Request body

``

```json
{
  "where": {
    "type": "eq",
    "field": "age",
    "value": 21
  }
}
```


## Response

`SearchObjectsResponseV2`

```json
{
  "data": [
    {
      "__rid": "ri.phonograph2-objects.main.object.5b5dbc28-7f05-4e83-a33a-1e5b851ababb",
      "__primaryKey": 1000,
      "__apiName": "Employee",
      "employeeId": 1000,
      "lastName": "smith",
      "firstName": "john",
      "age": 21
    }
  ]
}
```
