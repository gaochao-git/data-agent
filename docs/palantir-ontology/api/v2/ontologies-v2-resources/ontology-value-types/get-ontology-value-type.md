# Palantir API Reference — Get Ontology Value Type

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-value-types/get-ontology-value-type/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/valueTypes/{valueType}**

`operationId`: `v2.getOntologyValueType`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Gets a specific value type with the given API name.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `valueType` | true | string | The API name of the value type. To find the API name, use the **List value types** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Response

`OntologyValueType`

```json
{
  "apiName": "countryInitials",
  "description": "A three letter code for a top-level administrative region",
  "displayName": "Country Initials",
  "status": "ACTIVE",
  "fieldType": "string",
  "rid": "ri.type-registry.main.value-type.f5ee06ef-6dfd-4d91-a01e-91bd457c719d",
  "constraints": [
    {
      "type": "length",
      "length": {
        "minimumLength": 3,
        "maximumLength": 3
      }
    }
  ]
}
```
