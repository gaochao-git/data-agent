# Palantir API Reference — Create Temporary Object Set

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-object-sets/create-temporary-object-set/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/{ontology}/objectSets/createTemporary**

`operationId`: `v2.createTemporaryObjectSetV2`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Creates a temporary `ObjectSet` from the given definition. This `ObjectSet` expires after one hour.


Third-party applications using this endpoint via OAuth2 must request the following operation scopes: `api:ontologies-read api:ontologies-write`.

**OAuth scopes**: `api:ontologies-read`, `api:ontologies-write`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `branch` | false | string | The Foundry branch to reference. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The package version of the generated SDK. |
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Request body

``

```json
{
  "objectSet": {
    "type": "base",
    "objectType": "Employee"
  }
}
```


## Response

`CreateTemporaryObjectSetResponseV2`

```json
{
  "objectSetRid": "ri.object-set.main.temporary-object-set.c32ccba5-1a55-4cfe-ad71-160c4c77a053"
}
```
