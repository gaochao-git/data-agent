# Palantir Foundry Docs — /docs/foundry/vertex/read-only-mode

> 来源: https://www.palantir.com/docs/foundry/vertex/read-only-mode/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Read-only mode

In certain situations, Vertex graphs can be opened in read-only mode. In read-only mode, the following restrictions are applied:

  * New objects cannot be added to the graph (including via Search Around).
  * Graph nodes cannot be re-arranged (whether by drag-and-drop or other methods).
  * The toolbar at the top of the page is hidden.



## When are Vertex graphs opened in read-only mode?

Below is a non-exhaustive list of the situations where a graph is opened in read-only mode.

  * When a graph is embedded in Workshop and the read-only mode setting is explicitly enabled in the [widget configuration](/docs/foundry/vertex/embed-graph-workshop/#configure-the-widget).
  * When a graph is opened in [Carbon](/docs/foundry/carbon/overview/).
  * When a graph is embedded in [Notepad](/docs/foundry/notepad/widgets-vertex-graph/).



[âPREVIOUSEmbed a graph in a Workshop module](/docs/foundry/vertex/embed-graph-workshop/)

[NEXTMedia layers and image annotationsâ](/docs/foundry/vertex/media-layers/)