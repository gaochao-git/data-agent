# Palantir Foundry Docs — /docs/foundry/pipeline-builder/management-text-nodes

> 来源: https://www.palantir.com/docs/foundry/pipeline-builder/management-text-nodes/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Text nodes

You can add text nodes to your pipelines to help document and call attention to details in your graph. Text nodes render in the background of other graph nodes, allowing you to use them as section dividers or group labels to visually organize complex pipelines. Text nodes use [Markdown syntax ↗](https://www.markdownguide.org/cheat-sheet/), and they can be colored like regular nodes. They will not be affected by layout options and are not attached to any specific node on your graph.

## Add text nodes

1. To add a text node to your graph, select **Text** in the upper left of the graph.   
     
   ![Text button in the top left of the Pipeline Builder graph.](/docs/resources/foundry/pipeline-builder/management-text.png)
2. Double-click the text node to edit and add Markdown text. Changes are saved automatically as you type.   
     
    ![An example of selected teal nodes to be hidden in a pipeline.](/docs/resources/foundry/pipeline-builder/management-text-node.png?width=500)
3. To exit edit mode, use the keyboard shortcuts `Mod+Enter` or `Escape`, or select outside the text node. To discard your changes and revert to the previous version, use the discard option available while editing.

## Color text nodes

To color a text node, right click and select **Color nodes**. Choose a color or add a new color.

![The "Color nodes" menu option to color the text node.](/docs/resources/foundry/pipeline-builder/management-text-node-color.png)

You can also resize text nodes using the three lines in the bottom right corner. If the length of your text is greater than the length of the text node, the node will automatically become scrollable.

![The "Color nodes"" menu option to color the text node.](/docs/resources/foundry/pipeline-builder/management-text-node-scroll.png)

This feature is also supported in [Workflow Lineage](/docs/foundry/workflow-lineage/overview/).
