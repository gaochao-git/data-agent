# Palantir Foundry Docs — /docs/foundry/workshop/widgets-links

> 来源: https://www.palantir.com/docs/foundry/workshop/widgets-links/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Links

The Links widget is used to display the links relationship between objects and provide exploration into those paths. A module builder configuring a Links widget can:

- Choose the input object set which may contain one or more object types
- Select which specific links are expanded, displayed, sorted upon

The screenshot below shows an example of a configured Links widget displaying a single `Flight Alert`’s linked objects. The `Flight Alert`’s linked Departure Airport has been expanded further in this screenshot to show its links.

![Links widget example](/docs/resources/foundry/workshop/links_example.png?width=400)

## Configuration options

The following image shows the initial state of a Links widget and its configuration panel:

![links_empty_state](/docs/resources/foundry/workshop/links_empty_state.png)

A Links widget has the following configuration options:

- **Input data**
  - **Object set:** Determine the data that will be displayed within the widget. A module builder can either reuse an existing object set variable created elsewhere in this module or define a new object set variable inline. Selecting an object set here will make certain configuration options below available for configuration.
  - **Load data from scenario:** Enable the use of [scenario](/docs/foundry/workshop/scenarios-overview/) variables.
- **Link type configuration**
  - **Link types to display:** By default, all links are shown in the links widget. By choosing “Specify link types“, granular controls and features such as link level sorting can be configured. Additional details are available in the following section, [specified link configuration options](#specified-link-configuration-options).
  - **Default link expand:** Specify the number of links that will be auto-expanded by default in the first level.
- **Linked objects configuration**
  - **Enable exploration on link types:** At each link type level, enable a button to allow viewing the link type in Object Explorer.
    ![Open in Explorer button](/docs/resources/foundry/workshop/links_enable_explorer.png?width=450)
  - **Enable open object view on linked objects:** For each linked object, enable a button to allow opening the linked object's Object View in Object Explorer.
    ![Open Object View button](/docs/resources/foundry/workshop/links_enable_object_view.png?width=450)
  - **Enable object preview on hover:** When hovering over the title of a linked object, preview the linked object’s properties. By default, the popover includes the linked object’s prominent properties but can be configured for custom properties by specifying links.
    ![Link object preview window](/docs/resources/foundry/workshop/links_object_preview.png?width=450)

## Specified link configuration options

The following image shows the initial state of a Links widget and its configuration panel when **Specify link types** is selected:
![links_inner_empty_state](/docs/resources/foundry/workshop/links_inner_empty_state.png)

The Links widget's configuration panel options for specified links are the following:

- **Pick link type**
  - **Link type:** Once a starting object set has been selected, choose the link type from a dropdown to be displayed on the widget.
  - **Link type label override:** The link type’s label can be overridden with a new label for the link type.
- **Link type configuration**
  - **Sort linked object by:** Once a link type is chosen, a property and sorting direction can be configured.
- **Linked objects configuration**
  - **Display properties in object preview:** If "Enable object preview on hover" is enabled, the specified link type’s linked objects can be configured to show their prominent properties or specified properties in a preview popover.
