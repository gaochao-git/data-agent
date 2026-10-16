# Palantir Foundry Docs — /docs/foundry/object-explorer/apply-actions

> 来源: https://www.palantir.com/docs/foundry/object-explorer/apply-actions/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Apply Actions

In an Exploration, buttons in the top right of the perspective will categorize and list actions with relevance to your current object set or selection. The three main categories are **âActionsâ** for data writeback, **âOpen Inâ** for bringing your current exploration to another platform application, and **âExportâ** for bringing data out of the platform, such as to an Excel spreadsheet.

### Actions

[Action types](/docs/foundry/action-types/overview/) configured in the Ontology are displayed first with a name and description. Selecting one will open a form to allow you to fill in parameters and submit the Action. The current set of selected objects in your exploration (or all objects, if none are selected) is passed directly to the form, so only other parameters must be configured. Actions are unavailable if the number of selected objects exceeds 1000.

Object Explorer prefills action parameters with relevant selected objects. If there is uncertainty over which parameter to prefill, the decision is left to the user and no prefills are provided.

### Opening in other applications

If there are ways to open your current result set in another application, these will be displayed under the **" Open In"** heading.

### Export

Ways to export your object set, such as exporting to Excel or copying object IDs to your clipboard, appear here.

[âPREVIOUSSave lists](/docs/foundry/object-explorer/save-lists/)

[NEXTGenerate Object Explorer URLsâ](/docs/foundry/object-explorer/generate-urls/)