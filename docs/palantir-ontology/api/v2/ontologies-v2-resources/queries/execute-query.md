# Palantir API Reference — Execute Query

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/queries/execute-query/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/queries/{queryApiName}/execute**

`operationId`: `v2.executeQueryV2`

## Description

Executes a Query using the given parameters. By default, the latest version of the Query is executed. 
The latest version is the one that was most recently published, which may be a pre-release version.

Optional parameters do not need to be supplied.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `queryApiName` | true | string | The API name of the Query to execute. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `version` | false | string | The semantic version range of the Query to execute. Function execution selects a satisfying version from this range. If not specified, the version most recently published is used. |
| `branch` | false | string | The Foundry branch to execute the query from. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. When provided without `version`, the latest version on this branch is used, including pre-release versions. When provided with `version`, the specified version must exist on the branch. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `transactionId` | false | string | The ID of an Ontology transaction to read from. Transactions are an experimental feature and all workflows may not be supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to execute the query on. |


## Request body

``

```json
{
  "parameters": {
    "city": "New York"
  }
}
```


## Response

`ExecuteQueryResponse`

```json
{
  "value": [
    "EMP546",
    "EMP609",
    "EMP989"
  ]
}
```
