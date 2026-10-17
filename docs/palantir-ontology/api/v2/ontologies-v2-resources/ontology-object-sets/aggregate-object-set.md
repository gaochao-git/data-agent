# Palantir API Reference — Aggregate Object Set

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-object-sets/aggregate-object-set/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objectSets/aggregate**

`operationId`: `v2.aggregateObjectSetV2`

## Description

Aggregates the ontology objects present in the `ObjectSet` from the provided object set definition.


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
| `branch` | false | string | The Foundry branch to aggregate the objects from. If not specified, the default branch is used. Branches are an experimental feature and not all workflows are supported. |
| `transactionId` | false | string | The ID of an Ontology transaction to read from. Transactions are an experimental feature and all workflows may not be supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to aggregate the objects on. |
| `executeInMemoryOnly` | false | boolean | If true, the request fails with an error when it cannot be computed in-memory. Use this to opt into fast failure on requests that would otherwise require heavier computation.  Defaults to false. |


## Request body

``

```json
{
  "objectSet": {
    "objectType": "Employee",
    "type": "base"
  },
  "aggregation": [
    {
      "field": "tenure",
      "name": "min_tenure",
      "type": "min"
    },
    {
      "field": "tenure",
      "name": "avg_tenure",
      "type": "avg"
    }
  ],
  "groupBy": [
    {
      "field": "startDate",
      "ranges": [
        {
          "endValue": "2020-06-01",
          "startValue": "2020-01-01"
        }
      ],
      "type": "range"
    },
    {
      "field": "city",
      "type": "exact"
    }
  ]
}
```


## Response

`AggregateObjectsResponseV2`

```json
{
  "data": [
    {
      "metrics": [
        {
          "name": "min_tenure",
          "value": 1
        },
        {
          "name": "avg_tenure",
          "value": 3
        }
      ],
      "group": {
        "startDate": {
          "startValue": "2020-01-01",
          "endValue": "2020-06-01"
        },
        "city": "New York City"
      }
    },
    {
      "metrics": [
        {
          "name": "min_tenure",
          "value": 2
        },
        {
          "name": "avg_tenure",
          "value": 3
        }
      ],
      "group": {
        "startDate": {
          "startValue": "2020-01-01",
          "endValue": "2020-06-01"
        },
        "city": "San Francisco"
      }
    }
  ]
}
```
