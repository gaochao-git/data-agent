# Palantir Blog — Building with Palantir AIP: Data Tools for RAG / OAG

> 来源: https://blog.palantir.com/building-with-palantir-aip-data-tools-for-rag-oag-b3b509c8b0f3
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）
> 归档: https://web.archive.org/web/20250105174600id_/https://blog.palantir.com/building-with-palantir-aip-data-tools-for-rag-oag-b3b509c8b0f3

---

[![Palantir](https://miro.medium.com/v2/resize:fill:88:88/1*yFp1VmXHPcrYrYrDNlD_7A.png)](https://palantir.medium.com/?source=post_page---byline--b3b509c8b0f3--------------------------------)

[![Palantir Blog](https://miro.medium.com/v2/resize:fill:48:48/1*PbgkHC0TD-x4rvptc-w3jg.png)](https://blog.palantir.com/?source=post_page---byline--b3b509c8b0f3--------------------------------)

·


[Palantir Blog](https://blog.palantir.com/?source=post_page---byline--b3b509c8b0f3--------------------------------)

·

7 min read

·

Dec 5, 2023

--

# **Introduction**

Welcome to another installment of our Building with AIP series, where Palantir engineers and architects take you through how to build end-to-end workflows using our Artificial Intelligence Platform (AIP).  
   
Today we’re covering Ontology Augmented Generation (OAG), which is a more expansive, decision-centric version of Retrieval Augmented Generation (RAG). At a high level, RAG enables generative AI to retrieve data from outside sources. This allows LLMs to leverage context-specific external information — e.g., data on a business’ orders, customers, locations, etc. — to generate responses, reducing the risk of hallucinations. With RAG, LLMs can also cite the sources used to generate a particular response, building trust and providing a clear audit trail.  
   
OAG takes RAG to the next level, allowing LLMs to leverage deterministic logic tools (e.g., forecasts and optimizers) and actions to close the loop with source systems via the Palantir Ontology. Each enterprise’s ontology encompasses the data, logic, and actions that drive operational decision-making in that specific context. Grounding LLMs in the Ontology effectively anchors them in the reality of a given business, not only driving more accurate, powerful applications, but also building even greater trust: LLMs can effectively “show their work” and surface specific sources from the enterprise’s own operational reality.

## **The Application**

Like in our other cooking show videos, we start by showing the finished application we’ve built. In this scenario we have a fictional company — Titan Industries — that specializes in medical supplies confronting a fire at one of its distribution centers. The fire presents the risk of shortages for Titan’s customers, which we want to prevent.

With AIP, we can build an application — previewed below — that enables us to quickly assess the impact of the fire, identify the affected orders, and surface actionable solutions (e.g., redistributing inventory to fulfill customer orders). This application shows the Chain of Thought (CoT) reasoning steps that the LLM is taking, and which objects its accessing in the Ontology — providing transparency into how it arrived at its conclusion.

![]()

Let’s build!

## [HyperAuto](https://youtu.be/CiyLviRs4Ng?t=378&si=sc6ZgXSZLP1DTKWl)

In order to drive this OAG workflow, we need data elements that the LLM-backed functions can leverage.

HyperAuto — also known as Software-Defined Data Integration (SDDI) — is a suite of capabilities designed to provide end-to-end data integration out of the box, on top of the most common and mission-critical systems. HyperAuto enables you to autonomously create valuable workflows with ERP, CRM, and other organization-critical data.

Leveraging the metadata of the data source, HyperAuto queries the source in real-time to derive opinions on how syncs should be built, what transformation logic should be applied, and how to design an appropriate ontology.

By utilizing metadata for pipeline creation and connecting to complex systems like SAP, HyperAuto streamlines data operations, allowing analysts to concentrate on strategic goals. HyperAuto allows you to go from source to Ontology in a matter of minutes.

The Ontology in turn integrates real-time data from all relevant sources into a semantic model of the business. This enables us to anchor AI in the operational truth of the enterprise, mitigating the risk of model hallucinations and creating the trust needed for decision-making.

In this context, we use HyperAuto to create ontology objects such as customers, customer orders, finished goods, manufacturing plants, etc. from Titan’s SAP system — in minutes.

Learn more about HyperAuto [here](https://www.palantir.com/docs/foundry/hyperauto/architecture/).

![]()

## [Data Health](https://youtu.be/CiyLviRs4Ng?t=639&si=RAdm7Qrtz89dzP4l)

Now that data is flowing with HyperAuto, it’s important to keep it healthy and clean. AIP has a robust suite of integrated tools to ensure end-to-end data health and integrity checks, keeping data pipelines current and reliable.

When we combine Data Health with the Data Lineage tool, which provides a view of how data is flowing through the platform, we have a single pane of glass to view and inspect the health of the data across your entire enterprise. For example, in Data Health you can set up checks based on different criteria (e.g., status, time, size, content, schema) and set different severity and alert levels. With Data Lineage, we can easily see where a data health issue in one place may be causing issues elsewhere.

Learn more about Data Health [here](https://www.palantir.com/docs/foundry/data-health/overview/) and Data Lineage [here](https://www.palantir.com/docs/foundry/data-lineage/overview/).

![]()

## [Data as Code](https://youtu.be/CiyLviRs4Ng?t=807&si=Xp3xJgDAzhu6gkoO)

Palantir’s “data as code” philosophy infuses data management with the principles of software development, providing users with control, flexibility, and reproducibility.

In essence, AIP treats data with the same care and dynamic interactions as code, allowing for iterative improvements and meticulous change management in a multi-user environment.

Key to this system is the ability to branch — an idea that originates in version control systems — which allows multiple users to work on data simultaneously, fostering innovation without sacrificing data integrity. In addition, users can easily surface the temporal evolution of datasets, facilitating debugging and problem-solving.

This means users can move quickly and with confidence in the quality of their data.

Learn more about the data-as-code approach [here](https://www.palantir.com/docs/foundry/data-integration/branching/).

![]()

## [Securing the Ontology](https://youtu.be/CiyLviRs4Ng?t=1006&si=eZJ1C1i9mpno6thr)

Once we have created objects in our ontology, we need to not only define how they relate to the rest of your business with links and action types, but also ensure that they have the appropriate security controls for both users and AI.

We can do all this in Palantir’s Ontology Manager Application (OMA). OMA allows us to quickly propagate fine-grained security controls throughout your ontology, down to the level of individual objects. This safeguards enterprise information and ensures that users are able to tightly control the information that AI can access.

Learn more about managing Ontology security [here](https://www.palantir.com/docs/foundry/object-permissioning/managing-object-security/).

![]()

## [AIP Logic: Giving LLMs data tools](https://youtu.be/CiyLviRs4Ng?t=1240&si=JaZ9BppZq9W5i4aL)

Now that we have automatically created our data pipelines, defined our ontology, ensured that our data is clean, and set up our security controls, we’re ready to create our application.

We’ll do so using AIP Logic. AIP Logic revolutionizes the creation of AI-powered functions, offering a no-code environment that simplifies the integration of advanced LLMs with the Ontology. It is designed to streamline the development process, allowing builders to easily construct, test, and deploy AI-powered functions without delving into complex programming or tool configurations.

In the video, we show how AIP Logic allows us to equip the LLM with an Ontology-driven data tool — in this case, to help address the simulated supply chain issue at Titan Industries. The tools paradigm extends beyond data, to logic and action (as we’ll see in future videos), providing us the ability to safely “teach” the LLM new abilities — just like we would a new hire.

We start by inputting our prompts. Because we have given the LLM access to certain objects in our ontology, we are able to include them in our prompts. This means that the LLM also has access to these objects’ links, actions, and other relationships — for example, a customer order object would include the customer name, the material ID, the distribution center, etc., and all the relationships between them. The LLM is therefore able to take these relationships into account when generating its response.

AIP Logic’s user-friendly interface allows us to easily craft prompts, debug prompts and tool usage, and monitor outcomes. The different logic blocks that we have built prompt the LLM to search affected orders, identify distribution centers with adequate supply of the necessary materials, and return to me a list of affected orders and suggested remediations.

Within a few minutes, we’re able to deploy an application that is ready to be activated in the event of a distribution center setback and identify actionable solutions to issues that arise (in this example, telling me how to resolve shortages caused by a fire at a distribution center). This application, based on the principles of Ontology Augmented Generation (OAG), demonstrates the power of AIP to ground AI in an enterprise’s data, logic, and actions to support real-time operational decision-making.

![]()

# Conclusion

![]()

If you’re ready to unlock the power of full spectrum AI with AIP, sign up for an [AIP Bootcamp](https://www.palantir.com/platforms/aip/bootcamp/) today. Your team will learn from Palantir experts, and more importantly, get hands-on experience with AIP and walk away having assembled real workflows in a production environment.

Let’s build!   
Chad Wahlquist, Palantir Forward Deployed Architect
