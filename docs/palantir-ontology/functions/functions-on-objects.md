# Palantir Foundry Docs — /docs/foundry/functions/functions-on-objects

> 来源: https://www.palantir.com/docs/foundry/functions/functions-on-objects/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Functions on objects (FOO)

Functions in Foundry natively support accessing and modifying data from objects and links in the Ontology. After defining object and link types in the Ontology, you can import these types into a functions repository to have code bindings automatically generated. These code bindings include support for:

  * Passing object and object set types into a function as [parameters](/docs/foundry/functions/types-reference/#ontology-types)
  * Searching for object sets on demand
  * Modifying objects using [Ontology edits](/docs/foundry/functions/edits-overview/)



Because of this native support for the Ontology, functions in Foundry go far beyond commonly used Functions-as-a-Service (FaaS) platforms by providing native support for data storage, retrieval, and modificationâall subject to Foundry's guarantees for data security, lineage, and transparency.

The term "functions on objects" (sometimes referred to as "FOO") is used loosely to refer to functions that read object data, either as a parameter or using an object search, but there is no formal notion of a "function on objects" in Foundry as being distinct from any other function.

## Object APIs by language

The API you use to read objects depends on the language your function is written in:

  * TypeScript v1 functions search objects with the `Objects.search()` API exported from the `@foundry/ontology-api` package. Review [Object sets (TSv1)](/docs/foundry/functions/api-object-sets/) and [Objects and links (TSv1)](/docs/foundry/functions/api-objects-links/).
  * TypeScript v2 functions query the Ontology through the [Ontology SDK](/docs/foundry/ontology-sdk/overview/) client. Review [Getting started with TypeScript v2 functions](/docs/foundry/functions/typescript-v2-getting-started/), then the [TypeScript OSDK reference](/docs/foundry/ontology-sdk/typescript-osdk/) for the full query vocabulary.
  * Python functions query the Ontology through the [Ontology SDK](/docs/foundry/ontology-sdk/overview/) client. Review [Functions on objects in Python](/docs/foundry/functions/python-functions-on-objects/), then the [Python OSDK reference](/docs/foundry/ontology-sdk/python-osdk/).



If you have not yet chosen a language, review [Feature support by language](/docs/foundry/functions/language-feature-support/) and [TypeScript v1 compared to TypeScript v2](/docs/foundry/functions/language-feature-support/#typescript-v1-vs-typescript-v2).

[Learn how to get started with functions on objects.](/docs/foundry/functions/foo-getting-started/)

[âPREVIOUSTypeScript v1 / Use webhooks](/docs/foundry/functions/webhooks/)

[NEXTGetting startedâ](/docs/foundry/functions/foo-getting-started/)