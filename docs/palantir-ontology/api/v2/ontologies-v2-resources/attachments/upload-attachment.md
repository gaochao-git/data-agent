# Palantir API Reference — Upload Attachment

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/attachments/upload-attachment/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**POST /api/v2/ontologies/attachments/upload**

`operationId`: `v2.uploadAttachmentV2`

## Description

Upload an attachment to use in an action. Any attachment which has not been linked to an object via
an action within one hour after upload will be removed.
Previously mapped attachments which are not connected to any object anymore are also removed on
a biweekly basis.
The body of the request must contain the binary content of the file and the `Content-Type` header must be `application/octet-stream`.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-write`.

**OAuth scopes**: `api:ontologies-write`

## Query parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `filename` | true | string | The name of the file being uploaded. |


## Request body

``

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
