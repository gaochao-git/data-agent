# Palantir API Reference — List Property Attachments

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/attachment-properties/list-property-attachments/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/{ontology}/objects/{objectType}/{primaryKey}/attachments/{property}**

`operationId`: `v2.listPropertyAttachments`

## Description

Get the metadata of attachments parented to the given object.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `ontology` | true | string | The API name or RID of the Ontology. To find the API name or RID, use the **List Ontologies** endpoint or check the **Ontology Manager**. |
| `objectType` | true | string | The API name of the object type. To find the API name, use the **List object types** endpoint or check the **Ontology Manager**. |
| `primaryKey` | true | string | The primary key of the object containing the attachment. |
| `property` | true | string | The API name of the attachment property. To find the API name for your attachment, check the **Ontology Manager** or use the **Get object type** endpoint. |


## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `sdkPackageRid` | false | string | The package rid of the generated SDK. |
| `sdkVersion` | false | string | The version of the generated SDK. |
| `branch` | false | string | The Foundry branch to read from. If not specified, the default branch will be used. |


## Response

`AttachmentMetadataResponse`

```json
{
  "type": "single",
  "rid": "ri.attachments.main.attachment.bb32154e-e043-4b00-9461-93136ca96b6f",
  "filename": "My Image.jpeg",
  "sizeBytes": 393469,
  "mediaType": "image/jpeg"
}
```
