# Palantir API Reference — Media Reference Property basics

> 来源: https://www.palantir.com/docs/foundry/api/v2/ontologies-v2-resources/media-reference-properties/media-reference-property-basics/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

A MediaReference Property is a property that references a media item.

The Ontologies API currently returns only media references associated with a Media Set View.
Media references that identify a Media Set item directly, or reference a dataset file, are
omitted from object-loading responses. Clients may therefore treat the property as having no
value even though the media appears in Foundry.

In the underlying reference representation, `mediaSetViewItem` is supported; `mediaSetItem`
and `datasetFile` are not currently supported.
