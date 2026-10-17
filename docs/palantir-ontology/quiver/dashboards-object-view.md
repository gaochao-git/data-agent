# Palantir Foundry Docs — /docs/foundry/quiver/dashboards-object-view

> 来源: https://www.palantir.com/docs/foundry/quiver/dashboards-object-view/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Embed in an object view

Published Quiver dashboards can be embedded in [object views](/docs/foundry/object-views/overview/) in Object Explorer.

![Dashboard in object view](/docs/resources/foundry/quiver/quiver-dashboard-embedded-in-ov.png)

## Add Quiver dashboard widget

A Quiver dashboard can be added directly from a [“legacy” Object View builder tab](/docs/foundry/object-views/config-tabs/).

![Add tab](/docs/resources/foundry/quiver/object-view-add-tab.png?width=300px)

From there, open the **Add widget** menu and select **Quiver dashboard** from the list.

![Add widget](/docs/resources/foundry/quiver/object-view-add-widget.png)

![Add dashboard widget](/docs/resources/foundry/quiver/object-view-dashoard-widget.png?width=300px)

## Configure Quiver dashboard widget

From the dropdown list, select the published dashboard you want to embed.

![Dashboard dropdown menu](/docs/resources/foundry/quiver/object-view-select-dashboard.png?width=300px)

To use the object from the object view as an input to the Quiver dashboard, copy the name of the object input defined in the Quiver dashboard in the **Dashboard’s input name for object** field.

![Object input](/docs/resources/foundry/quiver/object-view-input-name.png?width=300px)

If there are additional inputs configured for this dashboard, you can map to the data inputs from the object view by selecting **Add Item** in the **Additional dashboard inputs** section.

| Quiver input type | Object view filter type |
| --- | --- |
| Boolean | String, or first in string list |
| Number | String, or first in string list |
| String | String, or first in string list |
| Time | String, or first in string list |
| Time Range | Time range or relative time range filter |
| Time Series | *Not supported* |
| Object | *Not supported* |
| Object Set | *Not supported* |
| String List | String list |

Finally, define the height of your widget. **This is required when the dashboard’s view style is set to stretch**, otherwise the widget will have a default height of 0 pixels.
