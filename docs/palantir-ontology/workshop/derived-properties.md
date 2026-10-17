# Palantir Foundry Docs — /docs/foundry/workshop/derived-properties

> 来源: https://www.palantir.com/docs/foundry/workshop/derived-properties/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Derived properties in Workshop

[Derived properties](/docs/foundry/ontology/derived-properties/) are properties whose values are calculated at query time from other properties or from linked objects. Workshop derived properties are **runtime-defined**. They are declared at the module level, per object type, and exist only within that module rather than being stored in the Ontology.

Runtime-defined derived properties allow builders to perform linked aggregations and calculations across properties directly in Workshop, without modifying the Ontology. Derived properties are compatible with a broader set of widgets than [function-backed columns](/docs/foundry/workshop/widgets-object-table/#sorting-on-function-backed-properties), including pivot tables, where they can be used for aggregation.

Derived properties are supported for [a subset of widgets and features](#limitations). Contact Palantir Support to discuss expanded support.

## Configuration

In the **Overview** tab, select **Derived properties** from the **Capabilities** section. You can also select the object type from the **Object types** section and add a derived property on the next screen.

![Derived properties configuration panel in Workshop.](/docs/resources/foundry/workshop/derived-properties-panel.png?width=200)

### Derived property types

**Linked property/aggregation:** Select a linked object property to display from a one-to-one link, or calculate an aggregation across a one-to-many link. Aggregations may only be calculated on the linked object type's native properties, not on other derived properties. You can also optionally apply statically defined object property filters on the linked object type.

![Example linked aggregation derived property type configuration.](/docs/resources/foundry/workshop/derived-properties-linked-aggregation.png?width=500)

For the full catalog of available aggregations and the source property types each accepts, review the [derived properties reference](/docs/foundry/ontology/derived-properties-reference/#aggregations).

**Column math:** Combine values from multiple properties on a single object type. You can reference the object type's native properties and linked property or aggregation derived properties. You cannot reference another column math derived property. For example, if `totalCompensation` is defined as `salary + bonus`, a second column math property cannot use `totalCompensation`; write the full calculation, such as `(salary + bonus) * 1.1`, in a single column math property instead.

![Example column math derived property type configuration.](/docs/resources/foundry/workshop/derived-properties-column-math.png?width=500)

Column math corresponds to the numeric and date expressions available to runtime-defined derived properties across the platform. [Review a full list of supported operations.](/docs/foundry/ontology/derived-properties-reference/#expressions)

## Performance considerations

Derived properties are computed at query time rather than stored, which may increase module computation times. Consider possible query latency when building performance-critical applications and workflows.

The platform can push many filters down to underlying object indexes, while sorting and some other operations require calculating derived values for the remaining candidate objects. Learn more about the [underlying behavior](/docs/foundry/ontology/derived-properties/#performance-and-scale) and [relative performance](/docs/foundry/ontology/derived-properties-reference/#scale-and-performance-by-aggregation) of each aggregation.

## Limitations

The limitations listed below are specific to Workshop. For platform-wide limitations, review [derived properties limitations](/docs/foundry/ontology/derived-properties/#limitations).

### Supported widgets

Derived properties support a subset of Workshop widgets, including the Object Table, Pivot Table, and Waterfall widgets. Hover over the **Derived properties** information icon for the complete list.

![Derived properties configuration panel in Workshop, info button highlighted.](/docs/resources/foundry/workshop/derived-properties-panel-info.png?width=200)

Contact Palantir Support if you need expanded support.

### Link traversal

Workshop derived properties support only a single link traversal. If you need a derived property that spans multiple links, consider an [ontology-defined derived property](/docs/foundry/object-link-types/derived-properties/#multi-hop-derived-properties), which supports up to three traversals, or a [function-backed column](/docs/foundry/workshop/widgets-object-table/#sorting-on-function-backed-properties) with custom traversal logic. You can also restructure your data pipeline to make the required properties available within one traversal.

### Sorting

When sorting object sets that use derived properties, the object set size is limited to 200 rows. For larger object sets that require sorting, consider expressing the derived property logic as a [function-backed column](/docs/foundry/workshop/widgets-object-table/#sorting-on-function-backed-properties) instead. Function-backed columns support end-user sorting for tables containing up to 10,000 objects. For tables exceeding 10,000 objects, end users cannot sort on the column, and a default sort on the function-backed column will only sort each page of objects as they are loaded.

### Saved states

Saved states do not support object sets that reference derived properties.
