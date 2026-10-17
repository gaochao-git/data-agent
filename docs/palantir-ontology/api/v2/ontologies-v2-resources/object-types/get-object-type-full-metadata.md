# Palantir API Reference — Get Object Type Full Metadata

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/object-types/get-object-type-full-metadata/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objectTypes/{objectType}/fullMetadata**

`operationId`: `v2.getObjectTypeFullMetadata`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets the full metadata for a specific object type with the given API name.


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
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to load the action type definition from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `includeDatasources` | false | boolean | When set to `true`, the `datasources` field on the returned object type is populated with the datasources backing it. Defaults to `false`. |


## Response

`ObjectTypeFullMetadata`
