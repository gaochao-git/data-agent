# Palantir API Reference — Get Attachment Content

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/attachments/get-attachment-content/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

**GET /api/v2/ontologies/attachments/{attachmentRid}/content**

`operationId`: `v2.getAttachmentContentV2`

## Description

Get the content of an attachment.


Third-party applications using this endpoint via OAuth2 must request the following operation scope: `api:ontologies-read`.

**OAuth scopes**: `api:ontologies-read`

## Path parameters

| 名称 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `attachmentRid` | true | string | The RID of the attachment. |


## Response

`body`
