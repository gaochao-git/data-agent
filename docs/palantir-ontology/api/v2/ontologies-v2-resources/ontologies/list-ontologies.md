# Palantir API Reference — List Ontologies

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/ontologies/list-ontologies/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies**

`operationId`: `v2.listOntologiesV2`

## Description

Lists the Ontologies visible to the current user.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Response

`ListOntologiesV2Response`

```json
{
  "data": [
    {
      "apiName": "default-ontology",
      "displayName": "Ontology",
      "description": "The default ontology",
      "rid": "ri.ontology.main.ontology.c61d9ab5-2919-4127-a0a1-ac64c0ce6367"
    },
    {
      "apiName": "shared-ontology",
      "displayName": "Shared ontology",
      "description": "The ontology shared with our suppliers",
      "rid": "ri.ontology.main.ontology.c61d9ab5-2919-4127-a0a1-ac64c0ce6367"
    }
  ]
}
```
