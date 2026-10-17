# Palantir API Reference — Get Attachment

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/attachments/get-attachment/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/attachments/{attachmentRid}**

`operationId`: `v2.getAttachmentV2`

## Description

Get the metadata of an attachment.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `attachmentRid` | true | string | The RID of the attachment. |


## Response

`AttachmentV2`

```json
{
  "rid": "ri.attachments.main.attachment.bb32154e-e043-4b00-9461-93136ca96b6f",
  "filename": "My Image.jpeg",
  "sizeBytes": 393469,
  "mediaType": "image/jpeg"
}
```
