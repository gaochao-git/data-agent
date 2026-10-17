# Palantir API Reference — Get Query Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/query-types/get-query-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/queryTypes/{queryApiName}**

`operationId`: `v2.getQueryTypeV2`

## Description

Gets a specific query type with the given API name.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `queryApiName` | true | string | The API name of the query type. To find the API name, use the **List query types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `version` | false | string | The version of the Query to get. If not specified, the latest version is used. The latest version is the one that was most recently published, including pre-release versions. |
| `branch` | false | string | The Foundry branch to load Query metadata from. Branches are an experimental feature and not all workflows are supported. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |


## Response

`QueryTypeV2`

```json
{
  "apiName": "getEmployeesInCity",
  "displayName": "Get Employees in City",
  "description": "Gets all employees in a given city",
  "parameters": {
    "city": {
      "dataType": {
        "type": "string"
      },
      "description": "The city to search for employees in",
      "required": true
    }
  },
  "output": {
    "dataType": {
      "type": "array",
      "subType": {
        "type": "object",
        "objectApiName": "Employee"
      }
    },
    "required": true
  },
  "rid": "ri.function-registry.main.function.f05481407-1d67-4120-83b4-e3fed5305a29b",
  "version": "1.1.3-rc1"
}
```
