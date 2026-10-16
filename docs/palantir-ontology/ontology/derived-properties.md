# Palantir Foundry Docs — /docs/foundry/ontology/derived-properties

> 来源: https://www.palantir.com/docs/foundry/ontology/derived-properties/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Derived properties

Derived properties are [properties](/docs/foundry/object-link-types/properties-overview/) whose values are calculated at query time from other properties or from [linked objects](/docs/foundry/object-link-types/link-types-overview/). Instead of storing a value, a derived property describes how to compute one: by traversing a link and selecting a property from the linked object, by aggregating across many linked objects, or by combining values arithmetically.

Because they are computed rather than stored, derived properties remain correct as the underlying data changes. For example, a derived property that counts linked `Employee` objects would reflect new hires and departures automatically, with no pipeline to rerun and no update logic to maintain.

A derived property can compute its value in one of three ways:

  * **Aggregate across linked objects:** A `Department` object type can expose the average salary of its linked `Employee` objects.
  * **Select from a linked object:** An `Employee` object type can expose the name of its manager, selected from a single linked `Employee` object.
  * **Calculate an expression:** An `Employee` object type can expose total compensation, calculated by adding `salary` and `bonus`.



Derived properties are **read-only**. They cannot be edited by actions or functions, and they are not stored in object storage.

## Ontology-defined and runtime-defined derived properties

Derived properties can be defined in the ontology or at runtime. The form you use determines where you configure it, who else can see it, and which capabilities are available.

| Ontology-defined| Runtime-defined  
---|---|---  
Configuration location| Ontology Manager| A Workshop module, Insight workbook, or an Ontology SDK request  
Storage| Ontology definition| Object set definition  
Visibility| Every consumer of the object type| The request or module that declares it, and users with access to a saved object set that contains it  
Maximum link traversals| 3| Workshop, Insight: 1; Ontology SDK: no fixed limit  
Expressions| Not supported| Supported  
Development status| Beta| Generally available  
  
Beta

Ontology-defined derived properties are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment. Functionality may change during active development. Runtime-defined derived properties are generally available.

Use **ontology-defined** derived properties when the calculation is part of how your organization models the object type, and every consumer should see it. Use **runtime-defined** derived properties when the calculation belongs to a single application or analysis, or when you need arithmetic and date expressions.

For a complete catalog of the available aggregations and expressions, review the [derived properties reference](/docs/foundry/ontology/derived-properties-reference/).

## Expressions and link-based derived properties

Link-based derived properties select or aggregate values from linked objects. Both ontology-defined and runtime-defined derived properties support link-based calculations.

Expressions perform arithmetic or date calculations using native properties or linked and aggregated derived properties. Expressions are available only for runtime-defined derived properties. A calculated property cannot reference another calculated property declared in the same `withProperties` operation; declare the dependent property in a subsequent operation instead.

## Availability

Application| Support  
---|---  
[Ontology Manager](/docs/foundry/object-link-types/derived-properties/)| Configure ontology-defined derived properties on an object type.  
[Workshop](/docs/foundry/workshop/derived-properties/)| Use ontology-defined derived properties, or define runtime-defined derived properties at the module level for a subset of widgets.  
[Ontology SDK](/docs/foundry/ontology-sdk/overview/)| Use ontology-defined derived properties, or define runtime-defined derived properties with the `withProperties` operation and use them in results, filters, sorts, and aggregations.  
[Insight](/docs/foundry/insight/table-insight/)| Use ontology-defined derived properties, or add runtime-defined derived properties to a table analysis.  
  
For the TypeScript Ontology SDK (OSDK 2.x), the minimum supported version of the `@osdk/client` package is `2.2.0-beta.x`. Selecting a property from a linked object and using arithmetic or date expressions require version `2.4.0` or later. Review the API documentation in [Developer Console](/docs/foundry/developer-console/overview/) for details.

## Derived properties security

A derived property inherits security from every object used to compute it. Access checks apply at each object in the calculation, including intermediate objects in a multi-hop traversal, before their values are combined.

As a result, two users **can see different values for the same derived property on the same object**. Each user's computed value reflects only the source and intermediate objects they are authorized to see.

Aggregations that collect values into a list or a set preserve the security markings of each collected value, so an individual value within the list carries its own markings. Review [property security markings](/docs/foundry/security/property-security-markings/) for more detail.

## Performance and scale

Derived property values are evaluated at query time rather than stored or indexed. However, the platform can optimize many queries by pushing filters down to the underlying object indexes, reducing the data that must be processed before the derived value is calculated.

Performance depends on the number of objects remaining after filters are applied, the number and intermediate result size of linked objects, the aggregation type, and the number of link traversals. Filtering or sorting by a derived value generally requires computing that value for the remaining candidate objects. Some joins and aggregations can also be pushed down, depending on the query.

For execution thresholds, fallback behavior, and query design guidance, review [Ontology query limitations](/docs/foundry/ontologies/oss-limitations/). For the compute cost of derived property queries, review [query compute usage](/docs/foundry/ontologies/query-compute-usage/). For guidance on choosing between a derived property and a precomputed value in your pipeline, review [Ontology structural guidance](/docs/foundry/ontology/ontology-structural-guidance/).

## Collection limits

The **Collect list** and **Collect set** aggregations accept a limit on how many values are returned. This limit governs what is returned to you, but it does not constrain what the platform evaluates.

Filters, sorts, and aggregations over a collected property run against the **complete** set of values, not the truncated list. An object can therefore match a filter on a value that does not appear in the list you receive.

Filters on a collected property check all underlying values, not just the ones within the collection limit. This means that a match can happen even when the value that satisfied the filter is not in the returned list; the returned list is truncated to the limit, but the filter is not. Reducers behave the same way: a reducer is equivalent to setting the collection limit to `1`, but it still filters against every constituent value, not just the one reduced value it returns.

## Limitations

Derived properties are under active development, and ontology-defined derived properties are in beta. The limitations below reflect current behavior.

### All derived properties

  * **Object Storage v1:** Queries with derived properties cannot include object types indexed using [OSv1](/docs/foundry/object-backend/osv1-osv2-migration/).
  * **Primary keys:** A primary key property cannot be a derived property.
  * **Required properties:** Derived properties cannot be marked as required.
  * **Value types:** A value type cannot be applied directly to a derived property. Derived properties do, however, inherit the value types of their source properties.
  * **Display formatting:** Derived properties cannot have conditional formatting rules or value formatting configured.
  * **Inline actions:** Derived properties cannot have inline actions configured.
  * **Automate:** [Automate](/docs/foundry/automate/overview/) conditions can filter on derived properties only when using [scheduled monitoring](/docs/foundry/automate/evaluation-frequency/#scheduled-monitoring). [Live monitoring](/docs/foundry/automate/evaluation-frequency/#live-monitoring) does not support derived properties.



### Ontology-defined derived properties

  * **Link traversals:** A maximum of three link traversals per derived property.
  * **Arithmetic and date expressions:** Not supported. Ontology-defined derived properties support link traversal and aggregation only. If you need arithmetic or date calculations, use a runtime-defined derived property instead.



### Runtime-defined derived properties

  * **Chained expressions:** An arithmetic or date expression cannot reference another expression declared in the same `withProperties` operation. Declare the dependent expression in a subsequent `withProperties` operation instead.



[âPREVIOUSSearch syntax](/docs/foundry/ontology/search-syntax/)

[NEXTDerived properties referenceâ](/docs/foundry/ontology/derived-properties-reference/)