# Palantir Foundry Docs — /docs/foundry/chatbot-studio/marketplace

> 来源: https://www.palantir.com/docs/foundry/chatbot-studio/marketplace/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Distribute AIP Chatbots using Marketplace

Use [Foundry DevOps](/docs/foundry/devops/overview/) to include your AIP Chatbots in [Marketplace products](/docs/foundry/devops/core-concepts/#product) for other users to install and reuse. [Learn how to create your first product.](/docs/foundry/foundry-devops/create-products/)

## Supported features

All AIP Chatbots features are supported by Marketplace products, with the exception of:

- [Assist agents](/docs/foundry/assist/agents-in-aip-assist/)

## Adding AIP Chatbots to Marketplace products

To add an AIP Chatbot to a product, first [create a product](/docs/foundry/foundry-devops/create-products/). In the **Add resources** step, search for and select your AIP Chatbot from the **Add files** option.

Alternatively, if you have a [Workshop application](/docs/foundry/workshop/overview/) that embeds an AIP Chatbot via the [AIP Chatbot widget](/docs/foundry/workshop/widgets-aip-chatbot/), you can add the Workshop module to the product and the AIP Chatbot will be included automatically.

## AIP Chatbots with document context

When packaging an AIP Chatbot that is configured to use [document context retrieval](/docs/foundry/chatbot-studio/retrieval-context/#document-context), the [media set](/docs/foundry/data-integration/media-sets/) containing the documents will automatically be included in the product. This ensures that the AIP Chatbot has access to the necessary documents when installed.

Media set content

The entire media set, including any items not used by the AIP Chatbot, will be packaged in the product. If you want to limit the content to only the documents used by the AIP Chatbot, you should create a new media set containing only the necessary documents and reconfigure the AIP Chatbot to use it.
