# Palantir Foundry Docs — /docs/foundry/workshop/unsupported-object-properties

> 来源: https://www.palantir.com/docs/foundry/workshop/unsupported-object-properties/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Unsupported object property types

Certain [Ontology properties](/docs/foundry/object-link-types/properties-overview/) are extremely large and will increase the loading time in a module. These "non-performant" properties are generally unsupported in Workshop. Information on certain Workshop widgets that support displaying unsupported properties can be found on this page.

The following property types are generally unsupported in Workshop:

- **Geoshape:** Geoshape properties are large JSON strings containing large coordinate arrays. As such, Workshop generally does not support geoshapes.
- **Vector:** Vector properties are large fixed-size float arrays. Viewing the values in a vector property is rarely necessary in operational workflows, since a user cannot typically derive meaning from the values in a vector. As such, Workshop generally does not support vectors.

## Widgets that display unsupported property types

Certain Workshop widgets can be configured to allow viewing of unsupported property types. For widgets that support viewing a property type on-demand, the unsupported property must be specifically revealed by a user action as described below. A warning icon with tooltip will also be displayed for unsupported properties during widget configuration describing its non-performance due to large size.

![adding_unsupported_property_type](/docs/resources/foundry/workshop/adding-unsupported-property-type.png)

The following widgets support viewing specific unsupported properties of an object on-demand:

- **[Object List](/docs/foundry/workshop/widgets-object-list/):** To view an unsupported property's value in an object list widget, select **Load** to reveal its value.   
    
  ![unsupported_property_in_object_list](/docs/resources/foundry/workshop/object-list-supported-property-type.png)
- **[Object Table](/docs/foundry/workshop/widgets-object-table/):** To view an unsupported property's value in an object table widget, select the **...** button to reveal its value.   
    
  ![unsupported_property_in_object_table](/docs/resources/foundry/workshop/object-table-unsupported-property-type.png)
- **[Property List](/docs/foundry/workshop/widgets-property-list/):** To view an unsupported property's value in a property list widget, select **Load** to reveal its value.   
    
  ![unsupported_property_in_property_list](/docs/resources/foundry/workshop/property-list-unsupported-property-type.png)

The following widgets support viewing unsupported properties of an object:

- **[Map](/docs/foundry/workshop/widgets-map/):** If a Geoshape property is configured in a Map widget, it will be automatically loaded.

## Related concepts

[Derived properties](/docs/foundry/workshop/derived-properties/) calculate property values at runtime based on links and aggregations. While derived properties also have performance considerations and limited widget support, they are computed values rather than native property types. See the [derived properties documentation](/docs/foundry/workshop/derived-properties/) for information on their specific limitations.
