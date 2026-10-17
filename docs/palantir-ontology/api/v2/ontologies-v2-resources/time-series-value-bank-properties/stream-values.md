# Palantir API Reference — Stream Values

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/time-series-value-bank-properties/stream-values/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/timeseries/{property}/streamValues**

`operationId`: `v2.streamValues`

## Description

Stream all of the points of a time series property (this includes geotime series references).


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object with the time series property. |
| `property` | true | string | The API name of the time series backed property. To find the API name, check the **Ontology Manager** or use the **Get object type** endpoint. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to read from. If not specified, the default branch will be used. |


## Request body

``

```json
{
  "range": {
    "type": "relative",
    "startTime": {
      "when": "BEFORE",
      "value": 5,
      "unit": "MONTHS"
    },
    "endTime": {
      "when": "BEFORE",
      "value": 1,
      "unit": "MONTHS"
    }
  }
}
```


## Response

`body`
