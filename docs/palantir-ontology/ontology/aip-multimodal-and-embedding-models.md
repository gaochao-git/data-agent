# Palantir Foundry Docs — /docs/foundry/ontology/aip-multimodal-and-embedding-models

> 来源: https://www.palantir.com/docs/foundry/ontology/aip-multimodal-and-embedding-models/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Choose multimodal and embedding models

This page provides guidance on choosing models for [semantic search](/docs/foundry/ontology/overview-semantic-search/) over documents, including documents that contain diagrams, charts, or tables. Available models change over time and differ between enrollments. For a list of supported models, review the [supported large language models (LLMs) and embedding models](/docs/foundry/aip/supported-llms/).

## Multimodal models

Text-only LLMs cannot read images. To answer questions about diagrams, charts, or other images, use a model with vision capabilities. To find these models, filter the [Model Catalog homepage](/docs/foundry/model-catalog/overview/#model-catalog-homepage) by **Type** and select **Vision model**.

You can use vision models in the following ways:

  * **Extract text from PDF documents:** Use [AIP Document Intelligence](/docs/foundry/document-intelligence/overview/) to compare optical character recognition (OCR) and vision language model (VLM) extraction strategies before you deploy one.
  * **Process images in a pipeline:** In Pipeline Builder, pass a media reference column to the [**Use LLM** node](/docs/foundry/pipeline-builder/pipeline-builder-llm/#vision) and select a model with the **Vision** label.



You can also use the extracted text for semantic search, then send the matching page images and the question to a vision model. To support this, store the page number and a [media reference](/docs/foundry/media-sets-advanced-formats/media-overview/#media-references) to an image of that page on each chunk object. Python transforms can [convert PDF pages to images](/docs/foundry/transforms-python/media-set-transforms-api/#convert_document_to_images).

## Embedding models

We recommend Palantir-provided embedding models. To find them, filter the [Model Catalog homepage](/docs/foundry/model-catalog/overview/#model-catalog-homepage) by **Type** and select **Embedding model** , or review the [supported text embedding models](/docs/foundry/aip/supported-llms/#available-text-embedding-models). If you cannot use Palantir-provided models, you can [register your own model](/docs/foundry/aip/bring-your-own-model/) and select it in [**Text to embeddings**](/docs/foundry/pipeline-builder/pipeline-builder-aip/#text-to-embeddings).

When you choose an embedding model, follow these guidelines:

  * **Use one model for chunks and queries:** Embed queries with the same model that you used for your chunks. If you switch models, embed all chunks again.
  * **Keep chunks within the model 's input limit:** Find the model's token limit on its [Model Catalog entity page](/docs/foundry/model-catalog/overview/#model-entity-page). The [**Chunk string**](/docs/foundry/ontology/document-processing/#chunking) board sets chunk size in characters. In English text, a [token](/docs/foundry/aip/aip-compute-usage/#tokens-in-aip) averages about four characters; other languages often use fewer characters per token, so leave extra headroom.
  * **Help short queries match longer passages:** A short question and the passage that answers it are different kinds of text, so their embeddings may not be close. To close this gap, try [hypothetical document embeddings (HyDE)](/docs/foundry/ontology/ontology-augmented-generation/#hyde-hypothetical-document-embeddings).
  * **Test on your own data:** Before you embed all your documents, compare candidate models on a sample of your documents and questions in each language that your users search in. You can use [AIP Evals](/docs/foundry/aip-evals/overview/) to test your semantic search function.



To embed images instead of text, use the [**Image to embeddings**](/docs/foundry/pb-functions-expression/imageToEmbeddingsV1/) expression in Pipeline Builder with an embedding model that supports vision capabilities.

[âPREVIOUSDocument processing](/docs/foundry/ontology/document-processing/)

[NEXTOntology augmented generationâ](/docs/foundry/ontology/ontology-augmented-generation/)