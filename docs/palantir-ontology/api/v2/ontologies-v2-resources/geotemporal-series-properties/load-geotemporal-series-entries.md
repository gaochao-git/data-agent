# Palantir API Reference — Load Geotemporal Series Entries

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/geotemporal-series-properties/load-geotemporal-series-entries/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/geotemporalSeries/{property}/loadEntries**

`operationId`: `v2.loadGeotemporalSeriesEntries`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Load the geotemporal series entries for a given object's geotemporal series reference property within the
specified time range.

Each entry in the response is a map of property names to values, following the same structure as
`OntologyObjectV2`. Use the `additionalProperties` field in the request to control which properties are included
in each entry depending on the underlying geotemporal integration.

Results are paginated. Use the `nextPageToken` from the response to retrieve subsequent pages.

:::callout{theme=warning title=Warning}
  Geotemporal series integrations with only "cold storage" enabled are not supported.
:::


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object with the geotemporal series property. |
| `property` | true | string | The API name of the geotemporal series property. To find the API name for your property, check the **Ontology Manager** or use the **Get object type** endpoint. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package RID of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Request body

``

```json
{
  "range": {
    "startTime": "2020-01-01T00:00:00Z",
    "endTime": "2020-06-01T00:00:00Z"
  },
  "additionalProperties": [
    "speed",
    "heading"
  ],
  "pageSize": 100
}
```


## Response

`LoadGeotemporalSeriesResponse`

```json
{
  "data": [
    {
      "time": "2020-03-06T12:00:00Z",
      "position": {
        "type": "Point",
        "coordinates": [
          -122.4194,
          37.7749
        ]
      },
      "speed": 120.5,
      "heading": 275.3
    },
    {
      "time": "2020-03-06T13:00:00Z",
      "position": {
        "type": "Point",
        "coordinates": [
          -122.4095,
          37.7845
        ]
      },
      "speed": 95.0,
      "heading": 180.0
    }
  ],
  "nextPageToken": "v1.QnVpbGQgdGhlIEZ1dHVyZQ"
}
```
