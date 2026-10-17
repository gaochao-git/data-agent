# Palantir Foundry Docs — /docs/foundry/chatbot-studio/getting-started

> 来源: https://www.palantir.com/docs/foundry/chatbot-studio/getting-started/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Getting started

This guide demonstrates how to access AIP Chatbot Studio, introduces the AIP Chatbot Studio interface, describes how to set up a basic AIP Chatbot equipped with the information and tools that you choose, and how to deploy and monitor the AIP Chatbot in production.

## Access AIP Chatbot Studio

AIP Chatbot Studio can be accessed from the platform’s workspace navigation bar or by using the quick search shortcuts `CMD + J` (macOS) or `CTRL + J` (Windows). Alternatively, you can create a new AIP Chatbot from your **Files** by selecting **+ New** and then selecting **AIP Chatbot**, as shown below.

![Create new AIP Chatbot window.](/docs/resources/foundry/chatbot-studio/new-chatbot.png?width=450)

After opening AIP Chatbot Studio, you can create a new AIP Chatbot file.

## Create an AIP Chatbot

AIP Chatbots are Palantir filesystem resources that have granular access control and can be created like any other filesystem resource, as shown in the image above, in the previous section.

You may also select the **New AIP Chatbot** option from within AIP Chatbot Studio.

![AIP Chatbot Studio landing page with New AIP Chatbot option.](/docs/resources/foundry/chatbot-studio/aip-chatbot-studio-home.png)

Alternatively, [create an AIP Chatbot from within AIP Threads.](/docs/foundry/threads/getting-started/#upgrade-a-thread-configuration-to-an-aip-chatbot)

## Set up an AIP Chatbot

![AIP Chatbot Studio creation page, showing chatbot name, description and file location choices.](/docs/resources/foundry/chatbot-studio/new-aip-chatbot-creation.png)

Add the name, description, and a photo as an avatar for your AIP Chatbot. This enables you to white-label your chatbot to fit the context of your application. If an avatar is not provided, a gray robot icon will be used as the default.

Next, you will need to configure the enterprise-specific information and tools that will be equipped to your AIP Chatbot, as detailed in the following sections.

### Types of information and tools

- **[Retrieval context](/docs/foundry/chatbot-studio/retrieval-context/):** Simple and fast, recommended for most use cases.
- **[Application state](/docs/foundry/chatbot-studio/application-state/):** To contextualize chatbots in [Workshop](/docs/foundry/workshop/overview/).
- **[Tools](/docs/foundry/chatbot-studio/tools/):** Used in complex and action-taking chatbots.

These configurations are what enable the LLM to be useful to your enterprise, your workflow, and your task.

### Choose a large language model (LLM)

The models available to you are a subset of those [enabled on your enrollment](/docs/foundry/aip/supported-llms/#llm-availability-prerequisites).

![AIP Chatbot Studio edit view, highlighting option to change the model.](/docs/resources/foundry/chatbot-studio/model-selection.png)

### Modify the system prompt

The [system prompt](/docs/foundry/chatbot-studio/core-concepts/#instructions-and-descriptions) should outline the AIP Chatbot's function within the context of the current application. By pressing `/` on your keyboard, you can refer to the configured [tools](/docs/foundry/chatbot-studio/tools/) and [application state](/docs/foundry/chatbot-studio/application-state/) and guide the AIP Chatbot on how to coordinate their usage. Make sure to describe the underlying business logic and the appropriate situations for using the right tools in context.

![AIP Chatbot Studio edit view, highlighting option to change the system prompt.](/docs/resources/foundry/chatbot-studio/system-prompt.png)

### Set the temperature

Users can modify the model temperature to determine the balance between focused, deterministic output (default value `0`) and random output (maximum value `1`).

![AIP Chatbot Studio edit view, highlighting option to set the model's temperature.](/docs/resources/foundry/chatbot-studio/temperature.png)

### Add conversation starters

You can also set up an input placeholder and suggested prompts to customize the chatbot for your intended workflow.

![AIP Chatbot Studio edit view, highlighting option to change the input placeholder and suggested prompts.](/docs/resources/foundry/chatbot-studio/conversation-starters.png)

### Save, view, and publish an AIP Chatbot

Once you have configured your AIP Chatbot, you can save your progress by using **Save** at the top right of the interface. You can add a description to your saved version by using the down arrow icon next to the **Save** option.

To view your AIP Chatbot in action in the perspective of an end-user interaction, use **View** and select the desired version.

When you are ready to deploy your AIP Chatbot, select **Publish** to make your chatbot available for use in production environments. You can also publish your [chatbot as a function](/docs/foundry/chatbot-studio/chatbots-as-functions/) by selecting the configuration icon next to the **Publish** option. This allows you to run your chatbot via [AIP Automate](/docs/foundry/automate/getting-started/) and AIP Evals.

![AIP Chatbot Studio save, view, and publish options.](/docs/resources/foundry/chatbot-studio/save-view-publish.png)

### Track AIP Chatbot feedback and usage

You can monitor the performance and usage of your chatbot through the **Monitoring** and **Usage** tabs, where you can see metrics and feedback from users. Feedback data is received from users giving thumbs-up or thumbs-down signs to the chatbot from a conversation.

Use in [AIP Threads](/docs/foundry/threads/getting-started/#aip-chatbot-mode), [Workshop](/docs/foundry/workshop/widgets-aip-chatbot/#configure-the-aip-chatbot), [view mode](#save-view-and-publish-an-aip-chatbot), or [OSDK](/docs/foundry/ontology-sdk/overview/) with [Developer Console](/docs/foundry/developer-console/create-application/) and [platform APIs](/docs/foundry/api/aip-agents-v2-resources/agents/agent-basics/).

![AIP Chatbot Studio edit mode usage tab.](/docs/resources/foundry/chatbot-studio/usage-tab.png)
