# Palantir API Reference — Get Interface Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-interfaces/get-interface-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/interfaceTypes/{interfaceType}**

`operationId`: `v2.getInterfaceType`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets a specific interface type with the given API name.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `interfaceType` | true | string | The API name of the interface type. To find the API name, use the **List interface types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |
| `branch` | false | string | The Foundry branch to load the interface type definition from. If not specified, the default branch will be used. Branches are an experimental feature and not all workflows are supported. |
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |


## Response

`InterfaceType`

```json
{
  "apiName": "Athlete",
  "displayName": "Athlete",
  "description": "Good at sportsball",
  "properties": {
    "name": {
      "rid": "com.palantir.property.d1abdbfe-0ce2-4fff-b0af-af21002c314b",
      "apiName": "name",
      "displayName": "Name",
      "dataType": "string"
    }
  },
  "extendsInterfaces": [
    "Human"
  ],
  "rid": "ri.ontology.main.interface.bea1af8c-7d5c-4ec9-b845-8eeed6d77482"
}
```
