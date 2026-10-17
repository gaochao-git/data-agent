# Palantir API Reference — Read Media Content

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/media-reference-properties/read-media-content/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/media/{property}/content**

`operationId`: `v2.readMediaContent`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets the content of a media item referenced by this property.

Supports a single byte range specified by the `Range` header as `bytes=start-end` or `bytes=start-`.
A satisfiable range returns `206 Partial Content` with `Content-Range`, `Accept-Ranges`, and the
content length of the returned range. An unsatisfiable range returns `416 Range Not Satisfiable`
with `Content-Range: bytes */<total length>` and an empty body. Unsupported or malformed ranges
are ignored, returning the full content with `200 OK`.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object with the media reference property. |
| `property` | true | string | The API name of the media reference property. To find the API name, check the **Ontology Manager** or use the **Get object type** endpoint. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to read from. If not specified, the default branch will be used. |
| `transactionId` | false | string | The ID of an Ontology transaction to read from. Transactions are an experimental feature and all workflows may not be supported. |
| `scenarioRid` | false | string | The resource identifier of an ontology scenario to read the media reference property from. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Response

`body`
