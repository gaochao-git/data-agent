# Palantir Foundry Docs — /docs/foundry/functions/model-aliases

> 来源: https://www.palantir.com/docs/foundry/functions/model-aliases/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Model aliases

Model aliases are named references to language models that provide a convenient way to reference language models in code.

For a full walkthrough of using language models in functions, see [Language models in TypeScript v2 and Python functions](/docs/foundry/functions/language-models-python-tsv2/).

## Define a model alias

To define a model alias, open a TypeScript v2 or Python code repository and follow the steps below:

  1. Open the **Platform SDK** tab in the **Resource imports** panel.



  2. To import a new language model, select **Add > Models** in the upper right corner. A window will open in which you can view available Palantir-provided and registered models.



  3. Select the models to import, then choose **Confirm selection**. A configuration dialog will open in which you can configure aliases for each selected model. Select the pen icon near the alias to make edits, or choose to keep the defaults. Alias keys must be unique within the repository.



  4. The imported models will appear in the **Platform SDK** tab in the **Resource imports** side panel. You can edit any alias inline by selecting the pen icon next to the alias.



## Use a model alias in code

To use a model alias in your function, import the alias utility and reference the alias by name. The alias resolves to a model RID that you can pass to a model client:

TypeScript v2Python
    
    
    Copied!
    
    1
    2
    3
    import { Aliases } from "@osdk/functions";
    
    const modelRid = Aliases.model("gpt5Nano").rid;
    
    
    Copied!
    
    1
    2
    3
    from functions.aliases import model
    
    model_rid = model("gpt5Nano").rid

For a complete example of calling a language model using an alias, see [Write a function that uses a language model](/docs/foundry/functions/language-models-python-tsv2/#write-a-function-that-uses-a-language-model).

Model aliases work in functions added to [Marketplace products](/docs/foundry/functions/marketplace-functions/), but cannot be remapped during installation. If the model referenced by an alias is not available in the target environment, the function will fail to resolve the alias at runtime.

## Related

You can also define [custom aliases](/docs/foundry/functions/custom-aliases/) for string values such as configuration parameters, feature flags, or environment-specific settings. Custom aliases appear in the **Platform SDK** tab and can make functions portable across environments.

[âPREVIOUSCustom aliases](/docs/foundry/functions/custom-aliases/)

[NEXTSource aliasesâ](/docs/foundry/functions/source-aliases/)