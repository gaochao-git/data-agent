# Palantir API Reference — Get Latest Value

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/time-series-value-bank-properties/get-latest-value/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/timeseries/{propertyName}/latestValue**

`operationId`: `v2.getLatestValue`

## Description

Get the latest value of a property backed by a timeseries. If a specific geotime series integration has both a history and a live integration, we will give precedence to the live integration.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object with the timeseries property. |
| `propertyName` | true | string | The API name of the timeseries property. To find the API name for your property value bank property, check the **Ontology Manager** or use the **Get object type** endpoint. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to read from. If not specified, the default branch will be used. |


## Response

`TimeseriesEntry`
