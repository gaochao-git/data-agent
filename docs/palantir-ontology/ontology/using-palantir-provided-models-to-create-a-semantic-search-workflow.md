# Palantir Foundry Docs — /docs/foundry/ontology/using-palantir-provided-models-to-create-a-semantic-search-workflow

> 来源: https://www.palantir.com/docs/foundry/ontology/using-palantir-provided-models-to-create-a-semantic-search-workflow/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Use Palantir-provided models to create a semantic search workflow

To use Palantir-provided language models, [AIP must first be enabled on your enrollment](/docs/foundry/aip/enable-aip-features/). You also must have permissions to use [AIP developer capabilities](/docs/foundry/platform-overview/aip-capabilities/). Using a custom model? Review [Using custom models to create a semantic search workflow](/docs/foundry/ontology/using-custom-models-to-create-a-semantic-search-workflow/) instead.

This page illustrates the process of building a notional end-to-end semantic search workflow using a [Palantir-provided embedding model](/docs/foundry/aip/supported-llms/).

## Instructions

To begin, you need to generate embeddings and store them in an object type with a [`vector` type](/docs/foundry/object-link-types/property-metadata/#property-base-types-with-limited-support). Then, you can set up a semantic search workflow in [Workshop](/docs/foundry/workshop/overview/), build an [AIP Chatbot Workshop widget](/docs/foundry/workshop/widgets-aip-chatbot/) solution, or create a custom semantic search function for use in [Workshop](/docs/foundry/workshop/overview/) and [AIP Logic](/docs/foundry/logic/overview/).

Prerequisite:

  * Generate embeddings and create object type



Options:

  * Create a simple semantic search workflow within Workshop using a KNN object set (no-code)
  * Enable an AIP Chatbot to semantic search for objects (no-code)
  * Create a function to semantically search across objects for use in Workshop and/or AIP Logic



## Generate embeddings and create object type

We will use [Pipeline Builder](/docs/foundry/pipeline-builder/overview/) to embed text in the dataset as vectors with the [**Text to Embeddings** expression](/docs/foundry/pipeline-builder/pipeline-builder-aip/#text-to-embeddings). The expression takes a string and converts it to a vector using one of the Palantir-provided models; in our case, this is the `text-embedding-ada-002` embedding model.

These embeddings can then be added to the Ontology as a vector property.

If you would like more control around the generation of embeddings using Palantir-provided models, see [Language models within Python transforms](/docs/foundry/transforms-python-spark/palantir-provided-models/#embeddings).

## Create a simple semantic search workflow within Workshop using a KNN object set (no-code)

The KNN object set cannot be sorted by relevance. If you need ordered results, use the function approach with TypeScript v1 or TypeScript v2. Both versions support relevance ordering.

Configuring a [KNN object set (TSv1)](/docs/foundry/functions/api-object-sets/#k-nearest-neighbors-knn) within [Workshop](/docs/foundry/workshop/overview/) is an easy no-code way to build a semantic search workflow.

  1. Create an object set [variable](/docs/foundry/workshop/concepts-variables/) and select the object type that contains an embedding property.
  2. Select the filter `+ On a property` option, then from the list of properties in the menu, select your embedding property.
  3. Once selected, the K-nearest-neighbors configuration should appear. If this configuration does not appear, verify that the property you selected is an embedding property.



Within this panel, you can configure:

  * **K-value:** A number between 1-100 for how many objects to return in the semantic search.
  * **Query:** The string variable to use as a query when performing the semantic search.


  4. Next, create a [text input](/docs/foundry/workshop/widgets-text-input/) widget and add its output variable to the KNN query option seen above.
  5. Lastly, add an [object table](/docs/foundry/workshop/widgets-object-table/) widget and configure its input variable to be the newly created [KNN object set](/docs/foundry/functions/api-object-sets/#k-nearest-neighbors-knn).



For more customized semantic search logic, see the section on functions.

## Use AIP Chatbot (no-code)

AIP Chatbots (formerly AIP Agents) created in [AIP Chatbot Studio](/docs/foundry/chatbot-studio/overview/) are good for beginning semantic searches across your objects because they do not require any code. Learn more about incorporating semantic search with more control over the functionality.

Follow the instructions on the [getting started](/docs/foundry/chatbot-studio/getting-started/) guide to create an AIP Chatbot and either add [Ontology context](/docs/foundry/chatbot-studio/retrieval-context/#ontology-context) or an **Ontology semantic search** [tool](/docs/foundry/chatbot-studio/tools/#types-of-tools). This initial setup will enable you to ask the AIP Chatbot to semantically search the objects.

## Create a function to semantically search across objects for use in Workshop and/or AIP Logic

Create a [functions repository](/docs/foundry/functions/getting-started/) and a function to query your object type. The goal is to take user input and run a [KNN search](/docs/foundry/functions/api-object-sets/#k-nearest-neighbors-knn) over the object type. In TypeScript v1, first generate a vector with the same Palantir-provided model used earlier. To learn how to import Palantir-provided models into a TypeScript v1 function, review [Language models in TypeScript v1 functions](/docs/foundry/functions/language-models/#embeddings). TypeScript v2 and Python accept the query as text, as described below.

Enabling vector properties for functions

Before proceeding, ensure that the entry `"enableVectorProperties": true` is present in the `functions.json` file in your Functions code repository. If this entry is not present, add it to `functions.json` and commit the change to proceed. Contact your Palantir representative if you need further assistance.

The `functions.json` configuration file is a TypeScript v1 repository artifact. No equivalent vector property setting is documented for TypeScript v2 or Python repositories, so do not assume this entry carries over to them. Contact your Palantir representative if a KNN search fails in a TypeScript v2 or Python repository.

### Example semantic search function

The code examples in this section are available in [TypeScript v1](/docs/foundry/functions/typescript-v1-getting-started/), [TypeScript v2](/docs/foundry/functions/typescript-v2-getting-started/), and Python functions; select the tab that matches your function version. TypeScript v1 defines each function as a method on an exported class, applies the `@Function()` decorator from `@foundry/functions-api` to that method, and queries the Ontology through `Objects.search()`. TypeScript v2 defines functions with `export default`, imports types from `@osdk/functions`, and queries the Ontology through an [Ontology SDK](/docs/foundry/ontology-sdk/typescript-osdk/) client passed as a parameter. Python uses the `@function` decorator and constructs an Ontology SDK client in the function body. For a full comparison, review the [TypeScript v1 versus TypeScript v2 comparison](/docs/foundry/functions/language-feature-support/#typescript-v1-vs-typescript-v2).

The three versions differ in where the query embedding is produced and in where the function must be defined:

  * **TypeScript v1** embeds the query itself with a Palantir-provided embedding model, then passes the resulting vector to `nearestNeighbors`. The function is a method on an exported class, conventionally defined in `functions-typescript/src/index.ts`.
  * **TypeScript v2** passes the user's text directly to `nearestNeighbors`, which accepts either a query vector or query text. No separate embedding call is required, so the two-step workflow collapses into one. The function must be the default export of a file in the `typescript-functions/src/functions` directory, and the file name must match the function name; review [Write a function](/docs/foundry/functions/typescript-v2-getting-started/#write-a-function) for the full requirements.
  * **Python** also accepts the query as text through [`nearest_neighbors`](/docs/foundry/ontology-sdk/python-osdk-migration/#nearest-neighbor-object-set-filtering). The method is a beta feature, so the function is declared with `@function(beta=True)`.



Generating an embedding as a separate step, as the TypeScript v1 example does with `TextEmbeddingAda_002.createEmbeddings`, has no documented TypeScript v2 equivalent. This workflow does not need one, because the query text is embedded during the KNN search. To call a Palantir-provided model directly from a TypeScript v2 or Python function for other purposes, review [Language models in TypeScript v2 and Python functions](/docs/foundry/functions/language-models-python-tsv2/).

The neighbor count limit differs between the TypeScript versions. TypeScript v1 restricts the k value to the range `0 < K <= 100`, while the TypeScript v2 `numNeighbors` argument accepts values from `1` to `500`.

TypeScript v1 sorts matches with a separate `.orderByRelevance()` call before taking the top k results. TypeScript v2 requests relevance ordering by passing `$orderBy: "relevance"` to `fetchPage()`. Python has no documented equivalent; instead, the neighbor count passed to the search bounds the results. Objects returned by the Python [`nearest_neighbors`](/docs/foundry/ontology-sdk/python-osdk-migration/#nearest-neighbor-object-set-filtering) method also carry a `_score` field.

The TypeScript v2 and Python examples below accept the same query text and neighbor count as the TypeScript v1 example and return the nearest objects. The TypeScript v2 function additionally receives an Ontology SDK client as its first parameter.

In each code snippet below, replace every instance of `ObjectApiName` with your unique object type. The identifier may sometimes appear as `objectApiName` with the first letter in lowercase.

TypeScript v1TypeScript v2Python
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    8
    9
    10
    11
    12
    13
    14
    15
    16
    17
    18
    19
    20
    21
    22
    import { Function, Integer } from "@foundry/functions-api";
    import { Objects, ObjectApiName } from "@foundry/ontology-api";
    import { TextEmbeddingAda_002 } from "@foundry/models-api/language-models"
    
    export class MyFunctions {
        @Function()
        public async findRelevantObjects(
            query: string,
            kValue: Integer,
        ): Promise<ObjectApiName[]> {
            if (query.length < 1) {
                return []
            }
            const embedding = await TextEmbeddingAda_002.createEmbeddings({inputs: [query]}).then(r => r.embeddings[0]);
    
            return Objects.search()
                        .objectApiName()
                        .nearestNeighbors(obj => obj.embeddings.near(embedding, {kValue: kValue}))
                        .orderByRelevance()
                        .take(kValue);
        }
    }
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    8
    9
    10
    11
    12
    13
    14
    15
    16
    17
    18
    19
    20
    import { Client, Osdk } from "@osdk/client";
    import { Integer } from "@osdk/functions";
    import { ObjectApiName } from "@ontology/sdk";
    
    export default async function findRelevantObjects(
        client: Client,
        query: string,
        kValue: Integer,
    ): Promise<Osdk.Instance<ObjectApiName>[]> {
        if (query.length < 1) {
            return [];
        }
    
        // nearestNeighbors embeds the query text, so no separate embedding call is needed.
        const page = await client(ObjectApiName)
            .nearestNeighbors(query, kValue, "embeddings")
            .fetchPage({ $pageSize: kValue, $orderBy: "relevance" });
    
        return page.data;
    }
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    8
    9
    10
    11
    12
    13
    14
    15
    16
    17
    18
    19
    from functions.api import function, Integer
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import ObjectApiName
    
    
    @function(beta=True)
    def find_relevant_objects(query: str, k_value: Integer) -> list[ObjectApiName]:
        if len(query) < 1:
            return []
    
        client = FoundryClient()
        # nearest_neighbors embeds the query text, so no separate embedding call is needed.
        return list(
            client.ontology.objects.ObjectApiName.nearest_neighbors(
                query=query,
                num_neighbors=k_value,
                vector_property=ObjectApiName.object_type.embeddings,
            )
        )

At this point, we have a function that can run semantic search to query objects with natural language. Remember to [publish the function](/docs/foundry/functions/getting-started/#publish-your-functions) so the function can be used anywhere within Foundry.

### Use semantic search functions in Workshop

  1. Start by [creating a Workshop application](/docs/foundry/workshop/getting-started/).
  2. Add a [text input widget](/docs/foundry/workshop/widgets-text-input/), which will be used as an input to the published KNN document fetch function.
  3. Add an [object list widget](/docs/foundry/workshop/widgets-object-list/) with an input [object set generated from the function](/docs/foundry/workshop/functions-use/#function-backed-variables-in-workshop) and the selected inputs as shown below:



  4. Set the neighbor count (`kValue` in TypeScript, `k_value` in Python) to however many results you want returned, subject to the limits described above. TypeScript v1 is additionally subject to the [specified limits](/docs/foundry/functions/api-object-sets/#k-nearest-neighbors-knn).



### Use semantic search functions in AIP Logic

Add the published function as a [tool](/docs/foundry/logic/getting-started/#use-a-logic-function) within AIP Logic. Instruct the language model to use the tool with a prompt similar to this:

> Use the findRelevantObjects tool with a `kValue` of 5 to find the most related objects. Remember to add quotes around query when using the tool.

[âPREVIOUSOntology augmented generation](/docs/foundry/ontology/ontology-augmented-generation/)

[NEXTUse custom models to create a semantic search workflowâ](/docs/foundry/ontology/using-custom-models-to-create-a-semantic-search-workflow/)