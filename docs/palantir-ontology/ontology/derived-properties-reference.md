# Palantir Foundry Docs — /docs/foundry/ontology/derived-properties-reference

> 来源: https://www.palantir.com/docs/foundry/ontology/derived-properties-reference/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Derived properties reference

This page catalogs the aggregations and expressions available to [derived properties](/docs/foundry/ontology/derived-properties/), noting where each can be used and how they compare in performance.

## Aggregations

An aggregation combines values from many [linked objects](/docs/foundry/object-link-types/link-types-overview/) into one value. An aggregation is required whenever a link traversal can reach more than one object. Both ontology-defined and runtime-defined derived properties support aggregations.

Linked selections and aggregations cannot embed another derived property definition, but their property selectors can reference a property that is already in scope. Expressions can use native properties or the results of linked selections and aggregations, as described below.

The following table lists each aggregation by its name in Ontology Manager and method name in the Ontology SDK, the property types it accepts as input, and the property type of the value it produces.

Name in Ontology Manager| Method name in Ontology SDK| Source property types| Result property type  
---|---|---|---  
Count| `$count`| Not applicable| Long  
Average| `avg`| Numeric| Double  
Sum| `sum`| Numeric| Numeric, widened (see below)  
Minimum| `min`| Numeric, string, Boolean, date, timestamp| Same as source  
Maximum| `max`| Numeric, string, Boolean, date, timestamp| Same as source  
Approximate cardinality| `approximateDistinct`| Numeric, string, Boolean, date, timestamp| Long  
Exact cardinality| `exactDistinct`| Numeric, string, Boolean, date, timestamp| Long  
Collect list| `collectList`| Anything except `vector`| Array of the source type  
Collect set| `collectSet`| Anything except `vector`| Array of the source type  
Not available| `approximatePercentile`| Numeric| Same as source  
  
In this table, **numeric** includes `byte`, `short`, `integer`, `long`, `float`, `double`, and `decimal`.

Each valid source property type can be scalar or an array of that type. Aggregations flatten array inputs before computing the result. For example, a percentile over an `array<integer>` property returns an `integer`.

### Result type widening

The **Sum** aggregation widens the numeric type results to avoid overflow:

Source type| Result type  
---|---  
`short`, `integer`| `long`  
`long`| `long`  
`float`| `double`  
`double`| `double`  
`decimal`| `decimal`, with increased precision and reduced scale (if necessary)  
  
### Collection behavior

The **Collect list** and **Collect set** aggregations share the following behavior:

  * Collected values do not have a guaranteed order unless [property reducers](/docs/foundry/object-link-types/property-reducers/) are defined. When property reducers are defined, Foundry uses them to sort the returned values whether or not the reducer is applied.
  * Objects with no value for the property are skipped, so the resulting array never contains nulls.
  * **Collect set** removes duplicate values. **Collect list** preserves them.
  * For **Collect set** , duplicates are removed before the limit is applied. The limit controls the number of **distinct** values returned rather than the **total** number of values considered.
  * The limit defaults to `10` and can be raised to a maximum of `100`.
  * The limit determines the values returned to you, not the values the platform evaluates. Learn more about [collection limits](/docs/foundry/ontology/derived-properties/#collection-limits) before filtering on a collected property.



A property reducer can select one representative value from a collected array for display and interface implementation. The reducer does not change the full array used by queries.

### Type limitations for aggregations

Vector properties cannot be aggregated or selected by a derived property. A derived property cannot produce a vector value.

## Expressions

Expressions combine values arithmetically or extract parts of a date. They are only available to [runtime-defined](/docs/foundry/ontology/derived-properties/#ontology-defined-and-runtime-defined-derived-properties) derived properties.

Static literals are not currently supported in expressions. Expression-based derived properties can only reference derived properties defined earlier in the declaration order; specifically, those defined in an inner object set expression. For example, when chaining multiple `withProperties` operations, a derived property can be referenced in a subsequent expression-based operation, but not within the same operation or any earlier one in the declaration order. Linked property- and aggregation-based derived properties cannot reference other derived properties at all.

### Numeric expressions

Ontology SDK| Description  
---|---  
`add`| Adds two values.  
`subtract`| Subtracts the second value from the first.  
`multiply`| Multiplies two values.  
`divide`| Divides the first value by the second.  
`max`| Returns the greater of two values.  
`min`| Returns the lesser of two values.  
`abs`| Returns the absolute value.  
`negate`| Reverses the sign of the value.  
  
### Date and timestamp expressions

Ontology SDK| Description  
---|---  
`min`| Returns the earlier of two values.  
`max`| Returns the later of two values.  
`extractPart`| Extracts a component of a date. Accepts `DAYS`, `MONTHS`, `QUARTERS`, or `YEARS`.  
  
Expressions require version `2.4.0` or later of the `@osdk/client` package.

## Choose to select or aggregate

Your ability to directly select a property depends on how many objects each link traversal can reach. If more than one object can be reached, an aggregation is required:

Link chain| Available operation  
---|---  
Every traversal reaches at most one object| Select a property directly or aggregate  
Any traversal can reach more than one object| An aggregation is required  
The chain includes a many-to-many link| An aggregation is required; selecting is never permitted  
A one-to-many link whose foreign key is an array| An aggregation is required; treated as potentially reaching many objects in either direction  
  
## Compare derived properties to native properties

Once defined, a derived property behaves much like a native property within the same request, with some differences:

Operation| Supported| Notes  
---|---|---  
Return in results| Yes| Derived properties must be selected explicitly, regardless of property type.  
Filter| Yes| Includes exact match, range, prefix, phrase, full-text, geographic, and relative date filters.  
Sort| Yes| In Workshop, sorting an object set that uses derived properties limits the set to 200 rows.  
Aggregate| Yes|   
Group by| Yes|   
Reference from another derived property| Limited| Linked property- and aggregation-based derived properties cannot reference other derived properties. Expression-based support depends on the declaration order, as described above.  
Edit with an action or function| No| Derived properties are read-only.  
Use as a primary key| No|   
Use as a link type foreign key| No|   
  
## Scale and performance by aggregation

Derived property values are evaluated at query time and are not indexed. Performance depends primarily on data scale: the number of objects evaluated and the number of linked objects each one reaches. Relative cost reflects the latency and compute usage of a request.

  * **Loading** a derived property, which means returning its value in results, is relatively inexpensive for every aggregation. The cost is roughly equivalent to an aggregation request.
  * **Searching** on a derived property, which means filtering or sorting by its value, is more expensive for most aggregations. **Collect list** and **Collect set** are the exception: the platform can often push searches on these aggregations down to existing indexes on the source property.



Aggregation| Load cost| Search cost| Notes  
---|---|---|---  
Count| Low| High|   
Sum, Average, Minimum, Maximum| Low| High|   
Approximate cardinality| Low| High| Prefer this over **Exact cardinality** unless an exact count is required.  
Exact cardinality| Moderate| High|   
Approximate percentile| Low| High| Runtime-defined only.  
Collect list, Collect set| Low| Low| Cost scales with the number of linked objects, not with the display limit.  
  
Additional guidance:

  * **Prefer approximate cardinality:** Exact cardinality is substantially less performant. Use it only when an exact count is required and the additional latency is acceptable.
  * **Consider traversal count and link cardinality:** Each traversal can increase the intermediate result size. Overall performance depends on both the data scale and the number of linked objects.
  * **Filter before aggregating:** Applying a filter to the linked object set reduces the number of objects the aggregation must process.
  * **Consider a precomputed value at high scale:** If a derived property introduces unacceptable latency, review [Ontology structural guidance](/docs/foundry/ontology/ontology-structural-guidance/#performance-considerations) for information on when denormalization is an appropriate tradeoff.



For platform-wide execution thresholds and fallback behavior, review [Ontology query limitations](/docs/foundry/ontologies/oss-limitations/). For compute cost, review [query compute usage](/docs/foundry/ontologies/query-compute-usage/).

[âPREVIOUSDerived properties](/docs/foundry/ontology/derived-properties/)

[NEXTOntology scenarios / Overviewâ](/docs/foundry/ontology/overview-ontology-scenario/)