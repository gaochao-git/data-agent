# Palantir Foundry Docs — /docs/foundry/aip-analyst/using-aip-analyst

> 来源: https://www.palantir.com/docs/foundry/aip-analyst/using-aip-analyst
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Using AIP Analyst

This page covers the features and concepts that make up an AIP Analyst session.

## Context

Context is the information AIP Analyst has access to during an analysis, such as your previous messages, the agent's tool results, and any Foundry resources you have added.

You can add resources to context manually using the **+** button in the input field. You can also drag and drop Foundry resources directly into the chat input area to add them as context. Dragging a resource URL from another browser tab or from within Foundry will automatically resolve the resource and load it into your analysis. Supported resources include datasets, object sets, Notepad documents, Workshop modules, functions, and more. You can also paste Foundry resource identifiers (RIDs) directly into the input to import them.

You can manage context manually using the [outline](#outline), or let AIP Analyst handle it using the [context cleanup](/docs/foundry/aip-analyst/capabilities/#planning) tool.

![Manually add context to AIP Analyst.](/docs/resources/foundry/aip-analyst/aip-analyst-manual-context-button.png)

## Settings

The **Settings** dialog is organized into four tabs: **General**, **Analysis**, **Models**, and **Skills**.

![The AIP Analyst 'Settings' menu.](/docs/resources/foundry/aip-analyst/aip-analyst-settings.png?width=600)

### General

General settings are application-wide preferences that apply across your analyses:

- **Appearance:** Set the color theme to light or dark.
- **Compact view:** Group thinking and tool activity into collapsed, expandable summaries with a status label that updates as AIP Analyst works. Your questions and the agent's text responses remain visible. Expand a summary to inspect its tool results.
- **Hide thinking details:** Hide the model's thinking content while keeping progress indicators visible.
- **Notifications:** If enabled, AIP Analyst can send notifications when in the background, allowing you to ask questions and be informed when an analysis requires your attention. Your browser and system settings must also allow notifications.
- **Unsaved analysis cost attribution:** Select the project that the cost of unsaved analyses is attributed to. Saved and embedded analyses are attributed based on their owning resource. See [Compute usage with AIP Analyst](/docs/foundry/aip-analyst/compute-usage/#cost-attribution) for details on how usage is attributed.

### Analysis settings

Analysis settings control search scope, initial context, and advanced agent behavior. Where these settings are stored depends on whether the analysis has been saved:

- **Unsaved analyses:** Changes apply to the current analysis and become your defaults for all new analyses.
- **Saved analyses:** The tab is marked with a **Current analysis** tag, and changes apply only to that [analysis resource](/docs/foundry/aip-analyst/analysis-resources/). To promote the current values to your defaults for new analyses, select **Save as user defaults** and confirm.

#### Limit search scope

Use these settings to restrict which Ontology entities AIP Analyst searches. When limits are set, the **Object type search** and **Object search** tools only return results that match. For an Ontology with hundreds or thousands of object types, applying these filters can improve performance.

- **Ontology:** Limit search to a single Ontology.
- **Object type groups:** Limit search to specific object type groups within the chosen Ontology.
- **Object type statuses:** Filter discoverable object types by status: `Endorsed`, `Active`, `Experimental`, `Deprecated`, or `Example`.
- **Object type visibility:** Filter discoverable object types by visibility: `Prominent`, `Normal`, or `Hidden`.
- **Projects:** Limit search to Ontology entities contained within specific projects. This setting does not apply to manually added context, and any selected folders that are not projects are ignored.

When you start a new, unsaved analysis directly in AIP Analyst, your **primary organization** can provide search scope defaults. Each setting you have not configured uses its organization default, if available. You can always override these [organization defaults](/docs/foundry/aip-analyst/admin-configuration/#default-analysis-search-scope). To restore organization defaults in an unsaved analysis, select **Reset all** in the organization defaults notice and confirm. This option appears when your settings override organization defaults.

Saved analyses and Workshop widgets use their own search scope configuration, including settings left unconfigured. User and organization defaults do not fill in these settings. When a saved analysis opens in a [Workshop widget](/docs/foundry/aip-analyst/workshop-widget/#tool-configuration), the widget's search scope takes precedence.

#### Initial context

These settings control the resources that are automatically added to AIP Analyst when it starts:

- **Include recent resources:** Include context about your recently viewed resources in the conversation.
- **Include favorite resources:** Include context about your favorite resources in the conversation.

#### Advanced

- **Object set tool similarity threshold:** Set the threshold for semantic search in the object set tool, between `0` (broadest) and `1` (exact). Higher values return fewer but more relevant results.

### Models

AIP Analyst offers three model categories: **Fast**, **Balanced**, and **Smart**. The AIP Analyst team updates the default models behind these categories as newer models become available. Use the model selector in the chat to switch categories for the current analysis.

In **Settings > Models**, choose a **Default model category** for new analyses. If you have not chosen one, AIP Analyst uses the [default category configured by your primary organization](/docs/foundry/aip-analyst/admin-configuration/#default-model-category), or **Balanced** if no organization default is configured. Changing this preference applies to new analyses; saved analyses retain their selected category.

Under **Default models**, you can set a category to **Custom** and choose a specific model for your analyses. Custom overrides do not receive automatic model updates, so you may miss newer, more capable models. Select **Default** for that category to resume automatic updates.

### Skills

The **Skills** tab lists the AIP skills available across your analyses. A skill packages reusable instructions for common workflows. For each enabled skill, AIP Analyst receives its name and a description of when to use it. The agent loads the full instructions only when it determines the skill is relevant to your question.

From this tab, you can:

- Select **Add skills** to add a skill to your library, or search across all skills you can access.
- Search your library by name.
- Toggle each skill on or off. When a skill is enabled, the agent can discover and invoke it on its own.
- Select a skill to open its detail panel, where you can review its name, description, and last update, or remove a skill you added yourself.

Skills assigned to your user groups also appear here. The **Added by** column identifies which groups assigned each skill. You can toggle inherited skills on or off to override their default enabled state, but they remain listed while assigned by a group. See [Group-level skills](/docs/foundry/aip-analyst/admin-configuration/#group-level-skills) for how administrators configure these defaults.

![The Skills settings show a skill added by the user and inherited from a group.](/docs/resources/foundry/aip-analyst/settings-dialog-skills-tab.png)

You can also create a skill from a conversation using the [export](#export) menu.

## Tabs and branching

An analysis path can be forked at any point, creating a new tab that only contains the prior context and enables users to explore multiple analysis paths from identical starting states. Empty analysis paths can be created using the **+** button in the tab header. Tabs will continue running even when not in focus.

![The option to create a new branch from a given message.](/docs/resources/foundry/aip-analyst/aip-analyst-branching.png)

## Outline

The analysis outline provides a structured summary of your session, displaying your questions, the agent's responses, manually added context, and tool usage. Icons distinguish messages and different types of tool calls.

You can use the outline to:

- Navigate quickly through prior analysis steps.
- Review token usage for each tool call. See [Monitor your usage](/docs/foundry/aip-analyst/compute-usage/#monitor-your-usage) for the other ways to track consumption.
- Hide specific tool results by selecting the eye icon that appears when hovering over outline items. Hidden results are no longer resent to the model on later turns, which reduces [token usage](/docs/foundry/aip-analyst/compute-usage/#keep-context-small).

![An AIP Analyst outline.](/docs/resources/foundry/aip-analyst/aip-analyst-outline.png)

## Graph

To improve confidence in AIP Analyst output, you can trace the analysis by reviewing the **graph** view, which is a directed graph showing the provenance and logic path of each step in the analysis. The graph view allows you to:

- Trace how the agent arrived at its conclusions and check the logic of each step.
- Audit data transformations applied during analysis and ensure reproducibility of results.
- Verify that results are grounded in actual data rather than hallucinations. With AIP Analyst, you can view the Ontology data that is used at each step and ensure that all conclusions are accurate.

![A sample AIP Analyst graph.](/docs/resources/foundry/aip-analyst/aip-analyst-graph.png)

## Audio input

You can record audio input directly from the chat interface, enabling voice-driven analysis.

## Export

Select the **Export** button in the session header to turn the current conversation into a durable artifact.

- **Print to PDF:** Export your conversation as a printable PDF document. The export dialog lets you select which context items to include, expand or collapse tool results, and preview the document before printing. This uses your browser's print dialog, allowing you to save the output as a PDF file.
- **Export to Notepad:** Export your conversation as shown in the preview to a Notepad document. Choose which messages to include and expand or collapse tool results before exporting. Messages are saved as text, with tool views included as images.
- **Summarize in Notepad:** Ask AIP Analyst to generate a summary of your conversation in a Notepad document.
- **Generate AIP Skill:** Turn your conversation into a reusable AIP skill, which AIP Analyst can then apply to similar questions in future analyses. See [skills](#skills) for more information.
- **Convert to Quiver:** Convert results into a Quiver analysis or dashboard.
- **Convert to Contour:** Convert results into a Contour analysis.

![The AIP Analyst export menu.](/docs/resources/foundry/aip-analyst/aip-analyst-export-menu.png)

If your **primary organization** requires a [watermark](/docs/foundry/aip-analyst/admin-configuration/#required-export-controls), it appears on every PDF export. You cannot disable the watermark or edit its text in the PDF export dialog.

In the [Workshop widget](/docs/foundry/aip-analyst/workshop-widget/#view-configuration), the PDF, Notepad, Quiver, and Contour targets can each be hidden individually.
