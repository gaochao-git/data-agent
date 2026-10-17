# Palantir API Reference — List Ontology Value Types

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontology-value-types/list-ontology-value-types/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/valueTypes**

`operationId`: `v2.listOntologyValueTypes`

## Description

:::callout{theme=warning title=Warning}
  This endpoint is in preview and may be modified or removed at any time.
  To use this endpoint, add `preview=true` to the request query parameters.
:::

Lists the latest versions of the value types for the given Ontology.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `preview` | false | boolean | A boolean flag that, when set to true, enables the use of beta features in preview mode. |


## Response

`ListOntologyValueTypesResponse`

```json
{
  "data": [
    {
      "apiName": "validNorthAmericanCountries",
      "description": "Options for valid countries in North America",
      "displayName": "Valid North American Countries",
      "status": "ACTIVE",
      "fieldType": "string",
      "rid": "ri.type-registry.main.value-type.f5ee06ef-6dfd-4d91-a01e-91bd457c719d",
      "constraints": [
        {
          "type": "enum",
          "enum": {
            "options": [
              "USA",
              "Mexico",
              "Canada"
            ]
          }
        }
      ]
    },
    {
      "apiName": "countyCode",
      "description": "A three letter code for a US County.",
      "displayName": "County Code",
      "status": "ACTIVE",
      "fieldType": "string",
      "rid": "ri.type-registry.main.value-type.f5ee06ef-6dfd-4d91-a01e-91bd457c8232",
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
  ]
}
```
