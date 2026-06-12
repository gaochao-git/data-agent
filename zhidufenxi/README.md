# 制度 PDF 正文抽取与条款切分交接说明

本目录只放交接文档，不放实现代码。

目标是在新的 Codex 会话中，于目标目录单独实现一个“制度 PDF 正文抽取 + 条款切分”工具，供后续制度冲突分析使用。

## 背景

- 制度文件总量约 `204` 份
- 文件类型为 `文本型 PDF`
- PDF 目录外部提供
- 目录 JSON 文件名为 `document_catlog.json`
- JSON 结构示例：

```json
{
  "公司治理": [
    "xxxx.pdf"
  ],
  "采购管理": [
    "yyyy.pdf"
  ]
}
```

## PDF 内容结构特征

大致结构如下：

1. 第 `1-2` 页通常是红头通知或制定说明，不是制度正文
2. 后续通常会出现 `附件`
3. 正文一般从 `第一章 总则`、`第一条 xxxx` 开始
4. 尾部常有附表、移交清单、目录清册、登记表等

## 本次要实现的范围

这次只做两件事：

1. `PDF 正文抽取`
2. `条款切分`

暂时不做：

- 大模型抽取
- 冲突分析
- OCR
- Web 界面

## 推荐落点

用户建议：

- 单独一个文件夹
- 或放到 `server/apps/complaint_app/` 下面

建议目录名：

- `zhidu_clause_extract`

## 推荐技术路线

- 先不用大模型
- 优先规则法
- PDF 读取优先使用 `PyMuPDF(fitz)`
- 若环境未安装，可回退 `pypdf`

## 推荐输出

至少输出：

- `documents.jsonl`
- `clauses.jsonl`
- `errors.jsonl`
- `raw_texts/*.txt`

## 你接下来要看的文档

- [实现规格](./IMPLEMENTATION_SPEC.md)
- [验收清单](./HANDOFF_CHECKLIST.md)
