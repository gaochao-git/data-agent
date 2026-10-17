# Palantir API Reference — Get Ontology

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontologies/get-ontology/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}**

`operationId`: `v2.getOntologyV2`

## Description

Gets a specific ontology for a given Ontology API name or RID.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |


## Response

`OntologyV2`

```json
{
  "apiName": "default-ontology",
  "displayName": "Ontology",
  "description": "The default ontology",
  "rid": "ri.ontology.main.ontology.c61d9ab5-2919-4127-a0a1-ac64c0ce6367"
}
```
