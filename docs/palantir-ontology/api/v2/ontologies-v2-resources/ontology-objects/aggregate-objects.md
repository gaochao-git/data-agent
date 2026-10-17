# Palantir API Reference — Aggregate Objects

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-objects/aggregate-objects/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objects/{objectType}/aggregate**

`operationId`: `v2.aggregateObjectsV2`

## Description

Perform functions on object fields in the specified ontology and object type.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The type of the object to aggregate on. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to aggregate objects from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |


## Request body

``

```json
{
  "aggregation": [
    {
      "type": "min",
      "field": "tenure",
      "name": "min_tenure"
    },
    {
      "type": "avg",
      "field": "tenure",
      "name": "avg_tenure"
    }
  ],
  "where": {
    "type": "eq",
    "field": "name",
    "value": "john"
  },
  "groupBy": [
    {
      "field": "startDate",
      "type": "range",
      "ranges": [
        {
          "startValue": "2020-01-01",
          "endValue": "2020-06-01"
        }
      ]
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
