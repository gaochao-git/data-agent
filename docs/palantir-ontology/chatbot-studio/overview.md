# Palantir Foundry Docs — /docs/foundry/chatbot-studio/overview

> 来源: https://www.palantir.com/docs/foundry/chatbot-studio/overview/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# AIP Chatbot Studio

**AIP Chatbot Studio** allows users to build interactive assistants, known as AIP Chatbots, that are equipped with enterprise-specific information and tools, deployable internally in the platform and externally through the [Ontology SDK](/docs/foundry/ontology-sdk/overview/) and [platform APIs](/docs/foundry/api/aip-agents-v2-resources/agents/agent-basics/). AIP Chatbot Studio was previously known as AIP Agent Studio, and AIP Chatbots were previously known as AIP Agents.

Chatbots built in AIP Chatbot Studio are powered by large language models (LLMs), the Ontology, documents, and custom tools. AIP Chatbots can be integrated into applications to facilitate dynamic, context-aware read and write workflows that enable you to automate tasks and reduce manual application interactions.

The following example shows an AIP Chatbot that uses an application variable to take a filtered object set of video transcripts as context when answering user questions about the recent press conference from the Federal Reserve.

![A screenshot of AIP Chatbot Studio edit page with the AIP Chatbot described above.](/docs/resources/foundry/chatbot-studio/agent-studio-edit-view.png)

The above AIP Chatbot can also be deployed in a [Workshop application](/docs/foundry/workshop/widgets-aip-chatbot/) that enables users to interact with the selected video.

![A screenshot of the AIP Chatbot described above deployed in a Workshop application.](/docs/resources/foundry/chatbot-studio/workshop-agent.png)

AIP Chatbot Studio is built on the same rigorous [security](/docs/foundry/security/overview/) model that governs the rest of the Palantir platform. These platform security controls grant an LLM access only to what is necessary to complete a task.
