# Palantir Foundry Docs — /docs/foundry/ontology-sdk/typescript-osdk

> 来源: https://www.palantir.com/docs/foundry/ontology-sdk/typescript-osdk
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# TypeScript OSDK

This page provides generic documentation for the TypeScript OSDK based on an example `Restaurant` object and its associated actions and queries. You can use Developer Console in the platform to [generate documentation based on your specific Ontology](/docs/foundry/developer-console/create-application/).

| Property | API name | Type |
| --- | --- | --- |
| `Restaurant Id` (primary key) | `restaurantId` | String |
| `Restaurant Name` (title) | `restaurantName` | String |
| `Address` | `address` | String |
| `Email` | `eMail` | String |
| `Number Of Reviews` | `numberOfReviews` | Integer |
| `Phone Number` | `phoneNumber` | String |
| `Review Summary` | `reviewSummary` | String |
| `Date Of Opening` | `dateOfOpening` | LocalDate |

The examples on this page target TypeScript OSDK 2.x. If you are upgrading from 1.x, review the [TypeScript OSDK migration guide](/docs/foundry/ontology-sdk/typescript-osdk-migration/) for the syntax changes, including the aggregation changes shown under [Aggregations](/docs/foundry/ontology-sdk/typescript-osdk-migration/#aggregations). Version-specific documentation is also available in the platform in Developer Console.

## Load single `Restaurant`

Parameters:

- **primaryKey `string`:** The primary key of the `Restaurant` you want to fetch

Example query:

TypeScript v2

```
Copied!

1
2
3
const result: Osdk.Instance<Restaurant> = await client(Restaurant).fetchOne(
  "primaryKey",
);
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
{
  "$primaryKey": "Restaurant Id",
  "$apiName": "Restaurant",
  "eMail": "Email",
  "restaurantId": "Restaurant Id",
  "address": "Address",
  "reviewSummary": "Review Summary",
  "phoneNumber": "Phone Number",
  "numberOfReviews": 123,
  "restaurantName": "Restaurant Name"
}
```

## Load a page of `Restaurants`

Load a page of objects. This automatically loads a single page of objects based on the specified page size.

This endpoint leverages the underlying object syncing technology used for the object type. If `Restaurant` is backed by Object Storage v2, there is no request limit. If `Restaurant` is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results; if more than 10,000 `Restaurants` have been requested, an `ObjectsExceededLimit` error will be thrown.

Example query:

TypeScript v2

```
Copied!

1
2
const page: PageResult<Osdk.Instance<Restaurant>> = await client(Restaurant)
  .fetchPage({ $pageSize: 30 });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "eMail": "Email",
      "restaurantId": "Restaurant Id",
      "address": "Address",
      "reviewSummary": "Review Summary",
      "phoneNumber": "Phone Number",
      "numberOfReviews": 123,
      "restaurantName": "Restaurant Name"
    }
    // ... Rest of page
  ]
}
```

## Load all `Restaurants`

Loads all `Restaurant` objects into an array. This uses an async iterator to fetch all objects across multiple pages.

This endpoint leverages the underlying object syncing technology used for the object type. If `Restaurant` is backed by Object Storage v2, there is no request limit. If `Restaurant` is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results; if more than 10,000 `Restaurants` have been requested, an `ObjectsExceededLimit` error will be thrown.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
async function getAll(): Promise<Array<Osdk.Instance<Restaurant>>> {
  const objects: Osdk.Instance<Restaurant>[] = [];
  for await (const obj of client(Restaurant).asyncIter()) {
    objects.push(obj);
  }
  return objects;
}

// If Array.fromAsync() is available in your target environment
function getAllFromAsync(): Promise<Array<Osdk.Instance<Restaurant>>> {
  return Array.fromAsync(client(Restaurant).asyncIter());
}
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
{
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "eMail": "Email",
      "restaurantId": "Restaurant Id",
      "address": "Address",
      "reviewSummary": "Review Summary",
      "phoneNumber": "Phone Number",
      "numberOfReviews": 123,
      "restaurantName": "Restaurant Name"
    }
    // ... Rest of data
  ]
}
```

## Fetch options

Use fetch options to control which properties and object metadata the TypeScript OSDK returns. The available options depend on the fetch method.

| Option | Supported methods | Description |
| --- | --- | --- |
| `$select` | `fetchOne()`, `fetchPage()`, `asyncIter()` | Return only the listed property API names. |
| `$applyModifiers` | `fetchPage()`, `asyncIter()` | Request a different returned representation for specific properties, such as a value selected by a configured reducer. |
| `$includeRid` | `fetchOne()`, `fetchPage()`, `asyncIter()` | Include the object's `$rid` in the result. |
| `$includeAllBaseObjectProperties` | `fetchPage()`, `asyncIter()` | For an interface object set, include all properties from the underlying concrete object type. This option has no effect on object type queries. |
| `$loadPropertySecurityMetadata` | `fetchOne()`, `fetchPage()`, `asyncIter()` | Include each property's security metadata under `$propertySecurities` on the returned object. |
| `$orderBy` | `fetchPage()`, `asyncIter()` | Order results by property API name, or by relevance for a nearest-neighbor query. |
| `$pageSize` | `fetchPage()` | Set the maximum number of objects to return in the page. |
| `$nextPageToken` | `fetchPage()` | Load the next page by passing the `nextPageToken` from the previous `PageResult`. |
| `$snapshot` | `fetchPage()` | Freeze the view at query time to prevent objects from being skipped or duplicated while paging. The default value is `false`, which loads the latest available results. |

### Select properties

For an object fetch, pass an array of property API names to `$select`. This differs from the `$select` object used for [aggregations](#aggregations). When fetch `$select` is omitted, the OSDK returns all native properties declared on the queried object type or interface. [Ontology-defined derived properties](/docs/foundry/ontology/derived-properties/#ontology-defined-and-runtime-defined-derived-properties) are not returned by default and must be included explicitly. Runtime-defined derived properties added with `withProperties()` are returned by default when `$select` is omitted; if you use `$select`, include their API names in the array.

The following example returns only `restaurantId` and `restaurantName`, with the standard object metadata:

TypeScript v2

```
Copied!

1
2
3
const page = await client(Restaurant).fetchPage({
  $select: ["restaurantId", "restaurantName"],
});
```

Selecting an array property returns its complete, unreduced value, even when a [property reducer](/docs/foundry/object-link-types/property-reducers/) is configured in Ontology Manager. The same behavior applies to native and ontology-defined derived properties.

### Apply property modifiers

Use `$applyModifiers` to change the returned representation of individual properties. The generated OSDK restricts each property to the modifiers supported by its Ontology configuration:

- **`"applyReducers"`:** For an array property with a configured reducer, return the selected array element instead of the full array.
- **`"applyMainValue"`:** For a struct property with a configured [main value](/docs/foundry/object-link-types/struct-main-fields/), return only the main value field or fields.
- **`"applyReducersAndExtractMainValue"`:** For a struct array configured with both features, select one struct with the reducer, then return its main value field or fields.

For example, assume an `Equipment` object type has a nullable `maintenanceHistory` property of type `Date` array, with a reducer configured to select the most recent date. Using `$select` without `$applyModifiers` returns the complete array:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
const page = await client(Equipment).fetchPage({
  $select: ["equipmentId", "maintenanceHistory"],
});

for (const equipment of page.data) {
  // string[] | undefined
  const fullMaintenanceHistory = equipment.maintenanceHistory;
}
```

Result:

```
Copied!

1
2
3
4
{
  "equipmentId": "E-100",
  "maintenanceHistory": ["2024-01-15", "2024-03-22", "2024-11-01"]
}
```

Set the property's modifier to `"applyReducers"` to request the reduced representation:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
const page = await client(Equipment).fetchPage({
  $select: ["equipmentId", "maintenanceHistory"],
  $applyModifiers: {
    maintenanceHistory: "applyReducers",
  },
});

for (const equipment of page.data) {
  // string | undefined
  const mostRecentMaintenanceDate = equipment.maintenanceHistory;
}
```

Result:

```
Copied!

1
2
3
4
{
  "equipmentId": "E-100",
  "maintenanceHistory": "2024-11-01"
}
```

The reduced result contains one element selected by the reducer rather than the full array. For an array of structs, `"applyReducers"` similarly returns the selected struct instead of the full struct array. Applying a modifier does not change the stored or derived property data. Omit the modifier when you need the complete value. The generated TypeScript type reflects the requested representation, changing from an array type to the array's element type when you apply the reducer.

## Load ordered results

Load an ordered page of `Restaurants` by specifying a sort direction for specific properties. When calling via APIs, sorting criteria are specified via the `fields` array. When calling via SDKs, you can specify ordering via the `$orderBy` parameter in the fetch options. The sort order for strings is case-sensitive, meaning numbers will come before uppercase letters, which will come before lowercase letters. For example, `Cat` will come before `bat`.

Parameters:

- **$orderBy `Record<string, "asc" | "desc">`:** An object specifying the property you want to order by and the direction ("asc" for ascending or "desc" for descending).

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
const page: PageResult<Osdk.Instance<Restaurant>> = await client(Restaurant)
  .fetchPage({
    $orderBy: { restaurantName: "asc" },
    $pageSize: 30,
  });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Object A",
      "$apiName": "Restaurant",
      "restaurantName": "A"
      // ...Rest of properties
    },
    {
      "$primaryKey": "Object B",
      "$apiName": "Restaurant",
      "restaurantName": "B"
      // ...Rest of properties
    }
    // ... Rest of page
  ]
}
```

## Links

Each link type defined on an object type generates an entry under the object instance's `$link` container, keyed by the link API name. The shape of that entry depends on the multiplicity of the link:

- A link with single multiplicity gives a `SingleLinkAccessor`, which exposes `fetchOne()` and `fetchOneWithErrors()`.
- A link with many multiplicity gives an `ObjectSet` of the linked type, so it supports the same methods as any other object set.

The examples below assume `Restaurant` has a single-multiplicity `city` link and a many-multiplicity `reviews` link. Substitute the link API names from your own Ontology.

Example query, reading a single-multiplicity link from an object instance:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
const restaurant = await client(Restaurant).fetchOne("primaryKey");

const city = await restaurant.$link.city.fetchOne();

// fetchOneWithErrors returns { value } or { error } instead of throwing.
// A missing linked object and any other failure both arrive as { error }.
const cityResult = await restaurant.$link.city.fetchOneWithErrors();
```

Example query, reading a many-multiplicity link as an object set:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
const restaurant = await client(Restaurant).fetchOne("primaryKey");

const reviews = await restaurant.$link.reviews.fetchPage({ $pageSize: 30 });

for await (const review of restaurant.$link.reviews.asyncIter()) {
  // ...
}
```

To traverse a link from an object set rather than from a single object instance, call `pivotTo()` with the link API name. It returns an object set of the linked type, so traversals can be chained and combined with filters and aggregations.

TypeScript v2

```
Copied!

1
2
3
const reviewsForNamedRestaurants = client(Restaurant)
  .where({ restaurantName: { $isNull: false } })
  .pivotTo("reviews");
```

## Filtering

The types of filtering you can perform depend on the types of the properties on a given object type. These filters can also be combined together via Boolean expressions to construct more complex filters.

This endpoint leverages the underlying object syncing technology used for the object type. If `Restaurant` is backed by Object Storage v2, there is no request limit. If `Restaurant` is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results; if more than 10,000 `Restaurants` have been requested, an `ObjectsExceededLimit` error will be thrown.

Parameters:

- **where `WhereClause` (optional):** Filter on a particular property. The possible operations depend on the type of the property. Filters are specified as an object with property names as keys and filter operators as values.
- **$orderBy `OrderBy` (optional):** Order the results based on a particular property. You can chain the `.where()` call with `fetchPage()` and pass `$orderBy` in the options to achieve the same result.

Example query:

TypeScript v2

```
Copied!

1
2
3
const page: PageResult<Osdk.Instance<Restaurant>> = await client(Restaurant)
  .where({ restaurantName: { $isNull: true } })
  .fetchPage({ $pageSize: 30 });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": null
      // ... Rest of properties
    }
    // ... Rest of page
  ]
}
```

### Types of search filters

#### Starts with

Only applies to String properties. Searches for `Restaurants` where `restaurantName` starts with the given string (case insensitive).

Parameters:

- **field:** Name of the property to use (for example, restaurantName).
- **$startsWith `string`:** Value to use for prefix matching against `Restaurant Name`. For example, "foo" will match "foobar" but not "barfoo".

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $startsWith: "foo" } });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": "foobar"
      // ... Rest of properties
    }
  ]
}
```

#### Contains any terms

Only applies to String properties. Returns `Restaurants` where `restaurantName` contains any of the white-space separated words (case insensitive) in any order in the provided value.

Parameters:

- **field:** Name of the property to use (for example, restaurantName).
- **$containsAnyTerm `string`:** White-space separated set of words to match on. For example, "foo bar" will match "bar baz" but not "baz qux".

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $containsAnyTerm: "foo bar" } });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": "foo bar baz"
      // ... Rest of properties
    },
    {
      "$primaryKey": "Restaurant Id 2",
      "$apiName": "Restaurant",
      "restaurantName": "bar baz"
      // ... Rest of properties
    }
  ]
}
```

#### Contains all terms

Only applies to String properties. Returns `Restaurants` where `restaurantName` contains all the white-space separated words (case insensitive) in any order in the provided value.

Parameters:

- **field:** Name of the property to use (for example, `restaurantName`).
- **$containsAllTerms `string`:** White-space separated set of words to match on. For example, "foo bar" will match "hello foo baz bar" but not "foo qux".

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $containsAllTerms: "foo bar" } });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": "hello foo baz bar"
      // ... Rest of properties
    }
  ]
}
```

#### Contains all terms in order

Only applies to String properties. Returns `Restaurants` where `restaurantName` contains all the terms (case insensitive) in the order provided and adjacent to each other.

Parameters:

- **field:** Name of the property to use (for example, `restaurantName`).
- **$containsAllTermsInOrder `string`:** White-space separated set of words to match on. For example, "foo bar" will match "hello foo bar baz" but not "bar foo qux".

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $containsAllTermsInOrder: "foo bar" } });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": "foo bar baz"
      // ... Rest of properties
    }
  ]
}
```

#### Range comparison

Only applies to Numeric, String and DateTime properties. Returns `Restaurants` where `Restaurant.restaurantName` is less than a value.

Parameters:

- **field:** Name of the property to use (for example, `restaurantName`).
- **value `string | number | date`:** Value to compare `Restaurant Name` to

Comparison types:

- Less than `$lt`
- Greater than `$gt`
- Less than or equal to `$lte`
- Greater than or equal to `$gte`

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $lt: "Restaurant Name" } });
```

#### Equal to

Only applies to Boolean, DateTime, Numeric, and String properties. Searches for `Restaurants` where `restaurantName` equals the given value.

Parameters:

- **field:** Name of the property to use (for example, `restaurantName`).
- **$eq `string | number | boolean | date`:** Value to do an equality check with `Restaurant Name`.

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $eq: "Restaurant Name" } });
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
{
  "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
  "data": [
    {
      "$primaryKey": "Restaurant Id",
      "$apiName": "Restaurant",
      "restaurantName": "Restaurant Name"
      // ... Rest of properties
    }
  ]
}
```

#### Null check

Only applies to Array, Boolean, DateTime, Numeric, and String properties. Searches for `Restaurants` based on whether a value for `restaurantName` exists or not.

Parameters:

- field: Name of the property to use (for example, `restaurantName`).
- $isNull `boolean`: Whether `Restaurant Name` exists. For checking that fields are non-null, use `$not` filter with `$isNull: true`.

Example query:

TypeScript v2

```
Copied!

1
2
const restaurantObjectSet = client(Restaurant)
  .where({ restaurantName: { $isNull: true } });
```

#### `Not` filter

Returns `Restaurants` where the query is not satisfied. This can be further combined with other boolean filter operations.

Parameters:

- $not: The search query to invert.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
const restaurantObjectSet = client(Restaurant)
  .where({
    $not: { restaurantName: { $isNull: true } },
  });
```

#### `And` filter

Returns `Restaurants` where all queries are satisfied. This can be further combined with other boolean filter operations.

Parameters:

- $and `Filter[]`: The set of search queries to `and` together.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
const restaurantObjectSet = client(Restaurant)
  .where({
    $and: [
      { $not: { restaurantName: { $isNull: true } } },
      { restaurantName: { $eq: "<primarykey>" } },
    ],
  });
```

#### `Or` filter

Returns `Restaurants` where any of the specified queries are satisfied. This can be further combined with other Boolean filter operations.

Parameters:

- $or `Filter[]`: The set of search queries to `or` together.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
const restaurantObjectSet = client(Restaurant)
  .where({
    $or: [
      { $not: { restaurantName: { $isNull: true } } },
      { restaurantName: { $eq: "<primarykey>" } },
    ],
  });
```

## Set operations

Object sets of the same object type can be combined. Each method takes one or more object sets and returns a new object set, so calls can be chained.

- `union()` returns the objects present in any of the given object sets.
- `intersect()` returns the objects present in all the given object sets.
- `subtract()` removes the objects present in the given object sets.

TypeScript v2

```
Copied!

1
2
3
4
5
6
const highlyReviewed = client(Restaurant).where({ numberOfReviews: { $gte: 100 } });
const named = client(Restaurant).where({ restaurantName: { $isNull: false } });

const either = highlyReviewed.union(named);
const both = highlyReviewed.intersect(named);
const onlyHighlyReviewed = highlyReviewed.subtract(named);
```

## Nearest neighbors

For an object type with a vector property, `nearestNeighbors()` narrows the object set to the closest matches. The example below assumes `Restaurant` has a vector property named `embedding`.

Parameters:

- **`query` `string | number[]`:** either a vector matching the embedding model defined on the property, or text, which is embedded automatically.
- **`numNeighbors` `number`:** the number of objects to return, limited to `1 <= numNeighbors <= 500`. If the object type holds fewer objects than this, all objects are returned.
- **`property`:** the property key with a defined embedding model to search over.

TypeScript v2

```
Copied!

1
2
3
const similar = await client(Restaurant)
  .nearestNeighbors("coffee", 5, "embedding")
  .fetchPage();
```

The example above returns the five nearest `Restaurants` in no guaranteed order. To order them by relevance and include each object's `$score`, pass `$orderBy: "relevance"` to `fetchPage()`.

## Aggregations

Aggregations allow you to compute summary statistics over a set of data. They are useful for understanding patterns and insights from large datasets without having to manually analyze each individual data point. You can combine multiple aggregation operations to create more complex queries that provide deeper insights into the data.

This endpoint uses the same object syncing technology as the object type. `Restaurant` can be backed by either Object Storage v2 or Object Storage v1 (Phonograph); in both cases, the endpoint returns a maximum of 10,000 results. If you request more than 10,000 `Restaurants`, the endpoint returns an `ObjectsExceededLimit` error.

Perform aggregations on `Restaurants`.

Selector keys are flat strings

Each key in `$select` takes one of two forms. It is either the literal `$count`, or a single string joining the property API name and the metric with a colon, such as `"numberOfReviews:avg"`. The metric carries no `$` prefix, and the key is never a nested object. Any other key is a type error.

Each value is the sort direction for that metric: `"unordered"`, `"asc"`, or `"desc"`. The examples below all use `"unordered"`. Ordering is available only when `$groupBy` names at most one property; with two or more group-by keys, every value must be `"unordered"`.

Parameters:

- **`$select`:** Set of aggregation functions to perform.
- **`$groupBy` (optional):** A set of groupings to create for aggregation results.

These two options are the only ones that `aggregate()` accepts. To restrict the objects being aggregated, chain a `.where()` call onto the object set before calling `.aggregate()`, as shown below.

The blocks labeled **Result** below show the value that `aggregate()` resolves to in TypeScript rather than the raw HTTP response. Unlike the object-loading endpoints above, the two differ for aggregations.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
const numRestaurants = await client(Restaurant)
  .where({ restaurantName: { $isNull: false } })
  .aggregate({
    $select: { $count: "unordered" },
    $groupBy: { restaurantName: "exact" },
  });
```

Result:

```
Copied!

1
2
3
4
// numRestaurants: Array<{ $group: { restaurantName: string }; $count: number }>
[
  { $group: { restaurantName: "Restaurant Name" }, $count: 100 },
]
```

A grouped aggregation returns an array of rows, and the array is empty when no objects match. Guard the index before reading: `numRestaurants[0]?.$group.restaurantName` gives the group value and `numRestaurants[0]?.$count` gives the metric. An ungrouped aggregation returns a single object instead of an array.

### Types of aggregations

The metrics available for a property depend on the type of that property:

- All property types: `approximateDistinct` and `exactDistinct`.
- Numeric properties: `sum`, `avg`, `min`, `max`, `approximateDistinct`, and `exactDistinct`.
- Date and timestamp properties: `min`, `max`, `approximateDistinct`, and `exactDistinct`. The `sum` and `avg` metrics are not available for these types.

Requesting a metric that the property type does not support, such as `"restaurantName:avg"`, is a type error. This check uses the property's base type and does not account for array-valued properties, so a metric requested on an array property compiles but may fail at runtime.

#### Approximate distinct

Computes an approximate number of distinct values for `restaurantName`.

Parameters:

- **`"<property>:approximateDistinct"`:** A single string key that joins the property API name and the metric with a colon (for example, `"restaurantName:approximateDistinct"`). The value is the sort direction for the metric, which is `"unordered"` in this example.

The TypeScript OSDK derives the result key from the property and the metric, so a metric cannot be given a custom name. The [Python OSDK](/docs/foundry/ontology-sdk/python-osdk/) documents a `name` parameter that aliases the computed metric.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
const distinctRestaurants = await client(Restaurant)
  .aggregate({
    $select: { "restaurantName:approximateDistinct": "unordered" },
  });
```

Result:

```
Copied!

1
2
// distinctRestaurants: { restaurantName: { approximateDistinct: number } }
{ restaurantName: { approximateDistinct: 100 } }
```

Read the value with `distinctRestaurants.restaurantName.approximateDistinct`. Both `approximateDistinct` and `exactDistinct` are typed `number`, with no `| undefined`, whatever the type of the property they count and whether that property is required.

#### Count

Computes the total count of `Restaurants`.

Parameters:

- **`$count`:** The only selector key that is `$`-prefixed and that names no property. The value is the sort direction for the metric, which is `"unordered"` in this example.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
const restaurantCount = await client(Restaurant)
  .aggregate({
    $select: { $count: "unordered" },
  });
```

Result:

```
Copied!

1
2
// restaurantCount: { $count: number }
{ $count: 100 }
```

Read the value with `restaurantCount.$count`. The `$count` key is always typed `number`, and it is present in the result only when you select it.

#### Numeric aggregations

Calculate the maximum, minimum, sum, or average of a property for `Restaurants`. The `sum` and `avg` metrics apply only to numeric properties; `min` and `max` also apply to date and timestamp properties, as listed under [Types of aggregations](#types-of-aggregations).

Parameters:

- **`"<property>:<metric>"`:** A single string key that joins the property API name and the metric with a colon (for example, `"numberOfReviews:avg"`). The value is the sort direction for the metric, which is `"unordered"` in this example.

Aggregation types:

- Average: `avg`
- Maximum: `max`
- Minimum: `min`
- Sum: `sum`

These metrics carry no `$` prefix; `$count` is the only `$`-prefixed selector key. As with approximate distinct, the result key is derived from the property and the metric, so metric aliases are not available in the TypeScript OSDK.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
const avgReviewScore = await client(Restaurant)
  .aggregate({
    $select: { "numberOfReviews:avg": "unordered" },
  });
```

Result:

```
Copied!

1
2
3
// Assuming numberOfReviews is optional, avg includes | undefined
// avgReviewScore: { numberOfReviews: { avg: number | undefined } }
{ numberOfReviews: { avg: 4.2 } }
```

Read the value with `avgReviewScore.numberOfReviews.avg`. Unlike the two distinct-count metrics, `sum`, `avg`, `min`, and `max` are typed as the underlying property's own value type and follow whether that property is required. For an optional `Integer` property, `avg` is therefore `number | undefined`, which is what the example above assumes for `numberOfReviews`. Properties whose Ontology SDK value type is a string yield a string instead. For the `LocalDate` property in the table above, `"dateOfOpening:max"` is typed `string | undefined`, as are metrics on `Long`, `Decimal`, and Timestamp properties. Handle the `undefined` case: `avg`, `min`, and `max` are `undefined` when the aggregation matches no objects.

To request several metrics in a single call, add a key for each.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
const stats = await client(Restaurant)
  .aggregate({
    $select: {
      $count: "unordered",
      "numberOfReviews:avg": "unordered",
      "numberOfReviews:max": "unordered",
      "restaurantName:exactDistinct": "unordered",
    },
  });
```

Result:

```
Copied!

1
2
3
4
5
6
7
8
9
10
// stats: {
//   $count: number;
//   numberOfReviews: { avg: number | undefined; max: number | undefined };
//   restaurantName: { exactDistinct: number };
// }
{
  $count: 100,
  numberOfReviews: { avg: 4.2, max: 123 },
  restaurantName: { exactDistinct: 87 },
}
```

Adding `$groupBy` to a call like this one changes the result to an array of rows. Each metric sits alongside `$group` in the row rather than inside it:

TypeScript v2

```
Copied!

1
2
3
4
5
// Array<{
//   $group: { restaurantName: string };
//   $count: number;
//   numberOfReviews: { avg: number | undefined; max: number | undefined };
// }>
```

### Types of group bys

#### Exact grouping

Groups `Restaurants` by exact values of `restaurantName`.

Parameters:

- **Property key:** The API name of the property to group by, used as the key in `$groupBy` (for example, `restaurantName`).
- **`"exact"`:** The value for that key, specifying exact grouping.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
const groupedRestaurants = await client(Restaurant)
  .aggregate({
    $select: { $count: "unordered" },
    $groupBy: { restaurantName: "exact" },
  });
```

Result:

```
Copied!

1
2
3
4
// groupedRestaurants: Array<{ $group: { restaurantName: string }; $count: number }>
[
  { $group: { restaurantName: "Restaurant Name" }, $count: 100 },
]
```

#### Numeric bucketing

Groups `Restaurants` by dividing `numberOfReviews` into buckets with the specified width.

Parameters:

- **Property key:** The API name of the property to group by, used as the key in `$groupBy` (for example, `numberOfReviews`).
- **`$fixedWidth` `number`:** Width of each bucket to divide the selected property into.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
const groupedRestaurants = await client(Restaurant)
  .aggregate({
    $select: { $count: "unordered" },
    $groupBy: { numberOfReviews: { $fixedWidth: 10 } },
  });
```

Result:

```
Copied!

1
2
3
4
5
// groupedRestaurants: Array<{ $group: { numberOfReviews: number }; $count: number }>
[
  { $group: { numberOfReviews: 0 }, $count: 100 },
  { $group: { numberOfReviews: 10 }, $count: 40 },
]
```

Each fixed-width group is reported as the plain start value of its bucket.

#### Range grouping

Groups `Restaurants` by specified ranges of `numberOfReviews`.

Parameters:

- **Property key:** The API name of the property to group by, used as the key in `$groupBy` (for example, `numberOfReviews`).
- **`$ranges` `Array<[number, number]>`:** Set of ranges, each written as a two-element `[start, end]` tuple with an inclusive start value and an exclusive end value. Both endpoints are required, so an open-ended range cannot be expressed.

Ranges also apply to date and timestamp properties, where each endpoint is an ISO 8601 string. A date property takes a plain date, as in `$ranges: [["2024-01-02", "2024-01-09"]]`, and a Timestamp property takes a full timestamp, as in `$ranges: [["2024-01-02T00:00:00Z", "2024-01-09T00:00:00Z"]]`.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
const groupedRestaurants = await client(Restaurant)
  .aggregate({
    $select: { $count: "unordered" },
    $groupBy: {
      numberOfReviews: {
        $ranges: [
          [0, 3],
          [3, 5],
        ],
      },
    },
  });
```

Result:

```
Copied!

1
2
3
4
5
6
7
8
// groupedRestaurants: Array<{
//   $group: { numberOfReviews: { startValue: number; endValue: number } };
//   $count: number;
// }>
[
  { $group: { numberOfReviews: { startValue: 0, endValue: 3 } }, $count: 50 },
  { $group: { numberOfReviews: { startValue: 3, endValue: 5 } }, $count: 30 },
]
```

Ranges are the only groupings reported as a `{ startValue, endValue }` object. Exact, fixed-width, and date/time groupings report a plain scalar typed as the property's own value type. For fixed-width and date/time groupings, that scalar is the start of the bucket rather than a value any object holds.

#### Datetime grouping

Groups `Restaurants` by `dateOfOpening` via buckets of a specific date/time duration.

Parameters:

- **Property key:** The API name of the property to group by, used as the key in `$groupBy` (for example, `dateOfOpening`).
- **`$duration` `[value, unit]`:** A two-element tuple of the number of units and the unit name.

Duration types available on a date property such as `dateOfOpening`:

- Days: `[n, "days"]`
- Weeks: `[1, "weeks"]`
- Months: `[1, "months"]`
- Quarters: `[1, "quarters"]`
- Years: `[1, "years"]`

The `weeks`, `months`, `quarters`, and `years` units accept a value of `1` only; `seconds`, `minutes`, `hours`, and `days` accept any number. Sub-day units (`seconds`, `minutes`, and `hours`) are available only on Timestamp properties, not on Date properties such as `dateOfOpening`.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
const groupedRestaurants = await client(Restaurant)
  .aggregate({
    $select: { $count: "unordered" },
    $groupBy: { dateOfOpening: { $duration: [10, "days"] } },
  });
```

Result:

```
Copied!

1
2
3
4
// groupedRestaurants: Array<{ $group: { dateOfOpening: string }; $count: number }>
[
  { $group: { dateOfOpening: "2024-09-25" }, $count: 100 },
]
```

## Actions on the Ontology

Action types in the Ontology refer to predefined operations that you can perform on objects within your data model. These actions can create, modify, and delete objects in the Ontology. Action types are generated based on the Ontology and can be used within the TypeScript OSDK to perform specific tasks on objects in the code.

### Parameters for adding a review to a `Restaurant` (`addRestaurantReview`)

| Property | API name | Type |
| --- | --- | --- |
| `Restaurant Id` | `restaurantId` | String |
| `Review Rating` | `reviewRating` | Integer |
| `Review Summary` | `reviewSummary` | String |

#### Apply action

To apply an action, fill in the input parameter values. This will execute an action and return if the response was valid or invalid.

Parameters:

- **parameters `Object`:** Object of parameter ID to values to use for those input parameters.
  - `restaurantId` `string`
  - `reviewRating` `number`
  - `reviewSummary` `string`
- **options (optional):** Options for the action execution.
  - **`$returnEdits` `boolean`:** Whether the edits are returned in the response after the action is applied.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
const result = await client(addReview).applyAction(
  {
    restaurantId: "restaurantId",
    reviewRating: 5,
    reviewSummary: "It was great!",
  },
  {
    $returnEdits: true,
  },
);

if (result.type === "edits") {
  console.log("Review added successfully", result);
} else {
  console.log("Review validation failed!", result);
}
```

Example API response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
23
24
25
26
27
28
29
30
31
32
33
34
35
36
37
38
{
  "validation": {
    "result": "VALID",
    "submissionCriteria": [],
    "parameters": {
      "restaurantId": {
        "result": "VALID",
        "evaluatedConstraints": [],
        "required": true
      },
      "reviewRating": {
        "result": "VALID",
        "evaluatedConstraints": [],
        "required": true
      },
      "reviewSummary": {
        "result": "VALID",
        "evaluatedConstraints": [],
        "required": true
      }
    }
  },
  "edits": {
    "type": "edits",
    "edits": [
      {
        "type": "modifyObject",
        "primaryKey": "restaurantId1",
        "objectType": "Restaurant"
      }
    ],
    "addedObjectCount": 0,
    "modifiedObjectsCount": 1,
    "deletedObjectsCount": 0,
    "addedLinksCount": 0,
    "deletedLinksCount": 0
  }
}
```

#### Apply batch action

To apply a batch of actions, fill in the input parameter values. This will execute a series of actions and return if the response was valid or invalid. The response contains only edits, not validations. Batch application is all-or-nothing: if any action in the batch fails, none of the edits in the batch are applied.

Parameters:

- **parameters `Array<Object>`:** Array of parameter objects with values to use for those input parameters.
  - `restaurantId` `string`
  - `reviewRating` `number`
  - `reviewSummary` `string`
- **options (optional):** Options for the action execution.
  - **`$returnEdits` `boolean`:** Whether the edits are returned in the response after the action is applied.

Example query:

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
const result = await client(addReview).batchApplyAction(
  [
    {
      restaurantId: "restaurantId1",
      reviewRating: 5,
      reviewSummary: "It was great!",
    },
    {
      restaurantId: "restaurantId2",
      reviewRating: 4,
      reviewSummary: "Good food but service can improve.",
    },
  ],
  {
    $returnEdits: true,
  },
);

if (result.type === "edits") {
  const updatedObject = result.editedObjectTypes[0];
  console.log("Edited Objects", updatedObject);
}
```

Example Response:

```
Copied!

1
2
3
4
5
6
7
8
9
10
11
12
13
14
15
16
17
18
19
20
21
22
{
  "edits": {
    "type": "edits",
    "edits": [
      {
        "type": "modifyObject",
        "primaryKey": "restaurantId1",
        "objectType": "Restaurant"
      },
      {
        "type": "modifyObject",
        "primaryKey": "restaurantId2",
        "objectType": "Restaurant"
      }
    ],
    "addedObjectCount": 0,
    "modifiedObjectsCount": 2,
    "deletedObjectsCount": 0,
    "addedLinksCount": 0,
    "deletedLinksCount": 0
  }
}
```

## Functions

[Functions](/docs/foundry/functions/overview/) (sometimes referred to as "functions on objects" or "FOO") in the Palantir platform are a powerful feature designed to enhance data modeling and manipulation. Functions provide a way to define and execute custom logic on the data stored in the Ontology, allowing users to create more sophisticated data transformations, validations, and analytics.

Within the TypeScript SDK, a user can execute Foundry Functions through generated function definitions.

By adding your functions to your application, you can generate code that calls functions on objects to execute logic and get the result.

In this example, we have a function `findSimilarRestaurants` that takes in an ID and returns an object set containing all the similar `Restaurants`.

### Parameters for executing a function to find similar `Restaurants` (`findSimilarRestaurants`)

| Property | API name | Type |
| --- | --- | --- |
| `Restaurant Id` | `restaurantId` | String |

Returns: `RestaurantObjectSet`

#### Apply function

To apply a function, you must execute it via the client. This is done by passing the function to the client and calling `executeFunction` with the parameters.

Example query:

TypeScript v2

```
Copied!

1
2
3
const result = await client(findSimilarRestaurants).executeFunction({
  restaurantId: "restaurantId",
});
```

## Use a scenario-scoped client

Experimental

Ontology scenario support is in the [experimental](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment. Functionality may change during active development.

OSDK can also be used to create and interact with [Ontology scenarios](/docs/foundry/ontology/overview-ontology-scenario/). You can use scenarios to build what-if analyses without changing the main Ontology. Create a scenario-scoped client or attach to an existing scenario by its RID. Foundry evaluates calls from this client against the scenario's isolated sandbox.

TypeScript v2

```
Copied!

1
2
3
4
5
6
7
8
9
import { createScenario, withScenario } from "@osdk/client/unstable-do-not-use";

// Create a new scenario. This is asynchronous because it calls the platform.
const scenario = await createScenario(client);

// Or attach to an existing scenario by RID. This is synchronous and makes no network call.
const existingScenario = withScenario(client, "ri.actions..scenario.0000-...");

const result = await scenarioClient(Restaurant).fetchOne("primaryKey");
```

To explore additional capabilities of the scenario-scoped client and check version requirements, review [Use scenarios with OSDK](/docs/foundry/ontology/osdk-scenario/).
