# Palantir Foundry Docs — /docs/foundry/ontology-sdk/python-osdk

> 来源: https://www.palantir.com/docs/foundry/ontology-sdk/python-osdk
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Python OSDK

This page provides generic documentation for the Python OSDK, based on an `Example Restaurant` object type. You can [generate documentation specific to your Ontology in Developer Console](/docs/foundry/developer-console/create-application/).

The examples on this page target Python OSDK 2.x. If you are upgrading from 1.x, see the [Python OSDK migration guide](/docs/foundry/ontology-sdk/python-osdk-migration/) for the syntax changes, including the required `.object_type` accessor. Version-specific documentation is also available in-platform in the Developer Console at `/workspace/developer-console/`.

The `Example Restaurant` object type has the following properties:

| Property | API name | Type |
| --- | --- | --- |
| `restaurant id` (primary key) | `restaurantId` | String |
| `restaurant name` (title) | `restaurantName` | String |
| `address` | `address` | String |
| `e mail` | `eMail` | String |
| `number of reviews` | `numberOfReviews` | Integer |
| `phone number` | `phoneNumber` | String |
| `review summary` | `reviewSummary` | String |

## On this page

| Section | What it covers |
| --- | --- |
| [Prerequisites](#prerequisites) | Installing your SDK and creating a `client`. |
| [Load single restaurant](#load-single-restaurant) | Fetching one object by primary key. |
| [Access object RID](#access-object-rid) | Reading the RID of a loaded object with `include_rid=True`. |
| [Load pages of example restaurants](#load-pages-of-example-restaurants) | Paging with `page_size` and `page_token`. |
| [Load all example restaurants](#load-all-example-restaurants) | Iterating over every object. |
| [Fetch options](#fetch-options) | Selecting properties, applying property modifiers, and controlling object metadata. |
| [Load ordered results](#load-ordered-results) | Sorting with `order_by`, `asc`, and `desc`. |
| [Filtering](#filtering) | Narrowing an object set with `where`, and the nine available [search filters](#types-of-search-filters-searchquery). |
| [Aggregations](#aggregations) | Computing summary statistics, and the three available [aggregation functions](#types-of-aggregations-aggregation). |
| [Types of group bys](#types-of-group-bys-groupby) | Grouping aggregation results. |
| [Use a scenario-scoped client](#use-a-scenario-scoped-client) | Creating or attaching to a scenario. |

## Prerequisites

Every example on this page assumes a `client` variable that is already connected to your Ontology, and a generated object class such as `ExampleRestaurant`. How you create the client depends on where your code runs.

### Inside Foundry

In a Foundry Python function, a Code Workspaces notebook, or a model adapter, the runtime supplies the hostname and credentials, so the constructor takes no arguments:

```
Copied!

1
2
3
from ontology_sdk import FoundryClient

client = FoundryClient()
```

### In a standalone Python application

Install your SDK using the values shown on your application **Overview** page in Developer Console, replacing each `< >` placeholder with your own value. The Python OSDK supports Python versions 3.10 through 3.14:

```
Copied!

1
2
export FOUNDRY_TOKEN=<YOUR-TOKEN-FROM-GETTING-STARTED-PAGE>
pip install <YOUR-PACKAGE-NAME> --upgrade --extra-index-url "https://:$FOUNDRY_TOKEN@<INDEX-URL>"
```

Then create the client with user token authentication:

```
Copied!

1
2
3
4
5
6
import os
from ontology_sdk import FoundryClient, UserTokenAuth

auth = UserTokenAuth(token=os.environ["FOUNDRY_TOKEN"])

client = FoundryClient(auth=auth, hostname="<YOUR-FOUNDRY-URL>")
```

For the full walkthrough, including certificate setup and troubleshooting, see [Bootstrap a new OSDK Python application](/docs/foundry/developer-console/how-to-bootstrapping-python/). For a backend service that authenticates as an application rather than as a user, see [Use the Ontology SDK with compute modules](/docs/foundry/compute-modules/osdk-integration/).

### Verify your setup

Using the `client` you created for your environment, confirm that it works before running any other example on this page:

```
Copied!

1
2
restaurants = client.ontology.objects.ExampleRestaurant.take(1)
print(restaurants)
```

In these examples, `ontology_sdk` is the generated package name for your Developer Console application, and `ExampleRestaurant` is a class generated from one of your object types. Substitute both for your own values. Your package name is shown on your application **Overview** page.

## Load single restaurant

Parameters:

- primaryKey `string`: The primary key of the `Example Restaurant` object you want to fetch.

Example query:

```
Copied!

1
result = client.ontology.objects.ExampleRestaurant.get("primaryKey")
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
    "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
    "__primaryKey": "Restaurant Id",
    "eMail": "E Mail",
    "restaurantId": "Restaurant Id",
    "address": "Address",
    "reviewSummary": "Review Summary",
    "phoneNumber": "Phone Number",
    "numberOfReviews": 123,
    "restaurantName": "Restaurant Name"
}
```

## Access object RID

By default, the object RID is not included as an accessible attribute on loaded objects. To access an object's RID programmatically, pass `include_rid=True` to your loading method:

```
Copied!

1
2
3
# Load a single object with RID access
result = client.ontology.objects.ExampleRestaurant.get("primaryKey", include_rid=True)
object_rid = result.rid
```

The `include_rid=True` parameter can be used with other loading methods as well:

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
# Iterate with RID access
for restaurant in client.ontology.objects.ExampleRestaurant.iterate(include_rid=True):
    print(restaurant.rid)

# Page with RID access
result = client.ontology.objects.ExampleRestaurant.page(page_size=30, include_rid=True)
for restaurant in result.data:
    print(restaurant.rid)
```

## Load pages of example restaurants

Load a list of objects of a requested page size, after a given page token if present.

This endpoint uses the underlying object syncing technology of the object type. If the `Example Restaurant` object type is backed by Object Storage v2, there is no request limit. If it is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results: requesting more than 10,000 `Example Restaurant` objects returns an `ObjectsExceededLimit` error.

Parameters:

- pageSize `integer` (optional): The size of the page to request, up to a maximum of 10,000. If not provided, the request loads up to 10,000 `Example Restaurant` objects. Subsequent pages use the `pageSize` of the initial page.
- pageToken `string` (optional): If provided, requests a page with a size less than or equal to the `pageSize` of the first requested page.

Example query:

```
Copied!

1
2
3
result = client.ontology.objects.ExampleRestaurant.page(page_size=30, page_token=None)
page_token = result.next_page_token
data = result.data
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "eMail": "E Mail",
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

## Load all example restaurants

Loads all `Example Restaurant` objects. In Python, `iterate()` returns an iterator. Wrap it in `list(...)` to collect every result, or use `take(n)` to fetch a fixed number of objects as a list.

This endpoint uses the underlying object syncing technology of the object type. If the `Example Restaurant` object type is backed by Object Storage v2, there is no request limit. If it is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results: requesting more than 10,000 `Example Restaurant` objects returns an `ObjectsExceededLimit` error.

Example query:

```
Copied!

1
2
objects_iterator = client.ontology.objects.ExampleRestaurant.iterate()
objects = list(objects_iterator)
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "eMail": "E Mail",
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

Python OSDK loading methods accept parameters that control which properties and object metadata are returned. The table below lists the synchronous methods; the corresponding asynchronous methods accept the same parameters.

| Parameter | Supported methods | Description |
| --- | --- | --- |
| `properties` | `get()`, `page()`, `iterate()`, `take()` | Return only the listed generated property references. |
| `apply_modifiers` | `get()`, `page()`, `iterate()`, `take()` | Request a different returned representation for specific properties, such as a value selected by a configured reducer. |
| `include_rid` | `get()`, `page()`, `iterate()`, `take()` | Populate the returned object's `rid` attribute. The default value is `False`. |
| `load_property_securities` | `get()`, `page()`, `iterate()`, `take()` | Load property-level security metadata for the returned object. The default value is `False`. |
| `load_ontology_defined_derived_properties` | `get()`, `page()`, `iterate()`, `take()` | Explicitly request or suppress loading of ontology-defined derived properties. |
| `page_size` | `page()` | Set the maximum number of objects in each page. |
| `page_token` | `page()` | Resume paging from a previous page iterator's `next_page_token`. |
| `num_items` | `take()` | Set the maximum number of objects to return. |

### Select properties

Pass generated property references to the `properties` parameter to restrict which properties are returned. When `properties` is omitted, the OSDK returns all properties normally loaded for the object type. The following example returns only `restaurant_id` and `restaurant_name`:

```
Copied!

1
2
3
4
5
6
7
result = client.ontology.objects.ExampleRestaurant.page(
    page_size=30,
    properties=[
        ExampleRestaurant.object_type.restaurant_id,
        ExampleRestaurant.object_type.restaurant_name,
    ],
)
```

Selecting an array property returns its complete, unreduced value, even when a [property reducer](/docs/foundry/object-link-types/property-reducers/) is configured in Ontology Manager. The same behavior applies to native and ontology-defined derived properties.

### Apply property modifiers

Beta

Property modifiers in the Python OSDK are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development. Functionality may change during active development.

Each generated object type with supported property configurations includes an `ApplyModifiers` class. Pass an instance of that class to `apply_modifiers`. The generated class restricts each property to the modifiers supported by its Ontology configuration:

- **`"applyReducers"`:** For an array property with a configured reducer, return the selected array element instead of the full array.
- **`"extractMainValue"`:** For a struct property with a configured [main value](/docs/foundry/object-link-types/struct-main-fields/), return only the main value field or fields.
- **`"applyReducersAndExtractMainValue"`:** For a struct array configured with both features, select one struct with the reducer, then return its main value field or fields.

If you also pass `properties`, include every property named in `apply_modifiers`. Otherwise, the OSDK raises a `ValueError`.

For example, assume an `Equipment` object type has a nullable `maintenance_history` property of type `Date` array, with a reducer configured to select the most recent date. Selecting the property without `apply_modifiers` returns the complete array:

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
equipment = client.ontology.objects.Equipment.get(
    "E-100",
    properties=[
        Equipment.object_type.equipment_id,
        Equipment.object_type.maintenance_history,
    ],
)

if equipment is not None:
    # list[date] | None
    full_maintenance_history = equipment.maintenance_history
```

To request the reduced representation, use the generated `EquipmentApplyModifiers` class:

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
from ontology_sdk.ontology.objects.equipment import EquipmentApplyModifiers

equipment = client.ontology.objects.Equipment.get(
    "E-100",
    properties=[
        Equipment.object_type.equipment_id,
        Equipment.object_type.maintenance_history,
    ],
    apply_modifiers=EquipmentApplyModifiers(
        maintenance_history="applyReducers",
    ),
)

if equipment is not None and equipment.modified_properties is not None:
    # date | None
    most_recent_maintenance_date = (
        equipment.modified_properties.maintenance_history
    )
```

When a modifier is applied, access the transformed value through `modified_properties`; the ordinary property attribute is `None`. In this example, `equipment.modified_properties.maintenance_history` contains one date selected by the reducer rather than the full array. Applying a modifier does not change the stored or derived property data. Omit the modifier and read the ordinary property attribute when you need the complete value.

## Load ordered results

Load an ordered list of `Example Restaurant` objects by specifying a sort direction for specific properties. When calling via APIs, you specify sorting criteria in the `fields` array. When calling via SDKs, you can chain multiple `order_by` calls together. The sort order for strings is case-sensitive, meaning that numbers come before uppercase letters, which come before lowercase letters. For example, `Cat` comes before `bat`.

Parameters:

- field `string`: The property you want to sort by. With the SDK, reference it as `ExampleRestaurant.object_type.<property>`.
- direction `asc` | `desc`: The direction you want to sort in, either ascending or descending. With the SDK, use the `asc()` and `desc()` methods on the property accessor.

Example query:

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
from ontology_sdk.ontology.objects import ExampleRestaurant

ordered_restaurants = (
    client.ontology.objects.ExampleRestaurant
    .where(~ExampleRestaurant.object_type.restaurant_name.is_null())
    .order_by(ExampleRestaurant.object_type.restaurant_name.asc())
    .iterate()
)
```

In this example, `~` negates the filter that follows it, so `~ExampleRestaurant.object_type.restaurant_name.is_null()` matches only the objects that have a `restaurant name` value. For the other Boolean operators available on filters, see [Not filter](#not-filter), [And filter](#and-filter), and [Or filter](#or-filter).

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
            "__primaryKey": "Object A",
            "restaurantName": "A"
            // ...Rest of properties
        },
        {
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Object B",
            "restaurantName": "B"
            // ...Rest of properties
        }
        // ...Rest of page
    ]
}
```

## Links

Link traversal takes one of two forms, depending on whether you start from a single object instance or from an object set. The examples below assume `Example Restaurant` has a many-to-one `city` link and a one-to-many `reviews` link. Substitute the link API names from your own Ontology.

On an object instance, each link is a method named after the link API name in snake case. A one-to-one or many-to-one link returns the linked object, or `None`. A one-to-many or many-to-many link returns an object set, so every object set method is available on it.

Example query:

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
restaurant = client.ontology.objects.ExampleRestaurant.get("primaryKey")

# A many-to-one link returns the linked object, or None.
city = restaurant.city()

# A one-to-many link returns an object set.
for review in restaurant.reviews().iterate():
    ...
```

On an object set, each link generates a `search_around_<link_name>()` method that returns an object set of the linked type, so traversals can be chained and combined with filters and aggregations.

Example query:

```
Copied!

1
reviews = client.ontology.objects.ExampleRestaurant.search_around_reviews()
```

If the link is only known at runtime, `search_around()` takes the link API name as a string and dispatches to the corresponding typed method. Prefer the typed `search_around_<link_name>()` methods where the link is known; `search_around()` raises a `RuntimeError` if the name is not a valid link for the object type.

Example query:

```
Copied!

1
linked = client.ontology.objects.ExampleRestaurant.search_around("reviews")
```

## Filtering

The types of filtering you can perform depend on the types of the properties on a given object type. You can also combine these filters with Boolean expressions to construct more complex filters.

This endpoint uses the underlying object syncing technology of the object type. If the `Example Restaurant` object type is backed by Object Storage v2, there is no request limit. If it is backed by Object Storage v1 (Phonograph), there is a limit of 10,000 results: requesting more than 10,000 `Example Restaurant` objects returns an `ObjectsExceededLimit` error.

Parameters:

- where `SearchQuery` (optional): Filter on a particular property. The possible operations depend on the type of the property.
- orderBy `OrderByQuery` (optional): Order the results based on a particular property. If using the SDK, you can chain the `.where` call with an `.order_by` call to achieve the same result.
- pageSize `integer` (optional): The size of the page to request, up to a maximum of 10,000. If not provided, the request loads up to 10,000 `Example Restaurant` objects. Subsequent pages use the `pageSize` of the initial page. If using the SDK, chain the `.where` call with the `.page` method and pass the `page_size` keyword argument.
- pageToken `string` (optional): If provided, requests a page with a size less than or equal to the `pageSize` of the first requested page. If using the SDK, chain the `.where` call with the `.page` method and pass the `page_token` keyword argument.

Example query:

```
Copied!

1
2
3
4
5
6
7
from ontology_sdk.ontology.objects import ExampleRestaurant

result = client.ontology.objects.ExampleRestaurant.where(
    ExampleRestaurant.object_type.restaurant_name.is_null()
).page(page_size=30)
page_token = result.next_page_token
data = result.data
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "restaurantName": null
            // ... Rest of properties
        }
        // ... Rest of page
    ]
}
```

### Types of search filters (`SearchQuery`)

The following filters are available, depending on the type of the property you filter on:

- [Contains any terms](#contains-any-terms)
- [Contains all terms](#contains-all-terms)
- [Contains all terms in order](#contains-all-terms-in-order)
- [Range comparison](#range-comparison)
- [Equal to](#equal-to)
- [Null check](#null-check)
- [Not filter](#not-filter)
- [And filter](#and-filter)
- [Or filter](#or-filter)

#### Contains any terms

Only applies to String properties. Returns `Example Restaurant` objects where `restaurantName` contains any of the whitespace-separated words (case-insensitive) in any order in the provided value.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `string`: Whitespace-separated set of words to match on. For example, `foo bar` matches `bar baz` but not `baz qux`.
- fuzzy `boolean`: Allows approximate matching in search queries.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name.contains_any_term(['foo bar']))
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
    "nextPageToken": "v1.000000000000000000000000000000000000000000000000000000000000000000000000",
    "data": [
        {
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "restaurantName": "foo bar baz"
            // ... Rest of properties
        },
        {
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000001",
            "restaurantName": "bar baz"
            // ... Rest of properties
        }
    ]
}
```

#### Contains all terms

Only applies to String properties. Returns `Example Restaurant` objects where `restaurantName` contains all the whitespace-separated words (case-insensitive) in any order in the provided value.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `string`: Whitespace-separated set of words to match on. For example, `foo bar` matches `hello foo baz bar` but not `foo qux`.
- fuzzy `boolean`: Allows approximate matching in search queries.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name.contains_all_terms(['foo bar']))
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "restaurantName": "hello foo baz bar"
            // ... Rest of properties
        }
    ]
}
```

#### Contains all terms in order

Only applies to String properties. Returns `Example Restaurant` objects where `restaurantName` contains all the terms (case-insensitive) in the order provided and adjacent to each other.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `string`: Whitespace-separated set of words to match on. For example, `foo bar` matches `hello foo bar baz` but not `bar foo qux`.
- `prefix_last_term` `boolean`: Set to `True` to match the final term as a prefix rather than as a whole word. Defaults to `False`.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name.contains_all_terms_in_order(['foo'], prefix_last_term=True))
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "restaurantName": "foo bar baz"
            // ... Rest of properties
        }
    ]
}
```

#### Range comparison

Only applies to Numeric, String, and DateTime properties. Returns `Example Restaurant` objects where `ExampleRestaurant.object_type.restaurant_name` is less than a value.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `string`: Value to compare `restaurant name` to.

Comparison types:

- Less than `<`
- Greater than `>`
- Less than or equal to `<=`
- Greater than or equal to `>=`

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name < "Restaurant Name")
```

#### Equal to

Only applies to Boolean, DateTime, Numeric, and String properties. Searches for `Example Restaurant` objects where `restaurantName` equals the given value.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `string`: Value to check `restaurant name` against for equality.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name == "Restaurant Name")
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
            "__rid": "ri.phonograph2-objects.main.object.00000000-0000-0000-0000-000000000000",
            "__primaryKey": "Restaurant Id",
            "restaurantName": "Restaurant Name"
            // ... Rest of properties
        }
    ]
}
```

#### Null check

Only applies to Array, Boolean, DateTime, Numeric, and String properties. Searches for `Example Restaurant` objects based on whether a value for `restaurantName` exists.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- value `boolean`: Whether `restaurant name` exists. In the Python SDK, `is_null()` takes no arguments. To find objects where the property is set, negate the filter with `~`, as in `~ExampleRestaurant.object_type.restaurant_name.is_null()`.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(ExampleRestaurant.object_type.restaurant_name.is_null())
```

#### Not filter

Returns `Example Restaurant` objects where the query is not satisfied. This can be further combined with other Boolean filter operations.

Parameters:

- value `SearchQuery`: The search query to invert.

Example query:

```
Copied!

1
2
3
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(~ExampleRestaurant.object_type.restaurant_id.is_null())
```

#### And filter

Returns `Example Restaurant` objects where all queries are satisfied. This can be further combined with other Boolean filter operations.

Parameters:

- value `SearchQuery[]`: The set of search queries to `and` together.

Example query:

```
Copied!

1
2
3
4
5
6
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(
    ~ExampleRestaurant.object_type.restaurant_id.is_null()
    & (ExampleRestaurant.object_type.restaurant_id == '<primaryKey>')
)
```

#### Or filter

Returns `Example Restaurant` objects where any of the specified queries are satisfied. This can be further combined with other Boolean filter operations.

Parameters:

- value `SearchQuery[]`: The set of search queries to `or` together.

Example query:

```
Copied!

1
2
3
4
5
6
from ontology_sdk.ontology.objects import ExampleRestaurant

example_restaurant_object_set = client.ontology.objects.ExampleRestaurant.where(
    ExampleRestaurant.object_type.restaurant_id.is_null()
    | (ExampleRestaurant.object_type.restaurant_id == '<primaryKey>')
)
```

## Aggregations

Perform aggregations on `Example Restaurant` objects.

Parameters:

- aggregation `Aggregation[]` (optional): Set of aggregation functions to perform. With the SDK, you can chain aggregation computations together with further searches using `.where`.
- groupBy `GroupBy[]` (optional): A set of groupings to create for aggregation results. If using the SDK, chain a `.group_by` call.
- where `SearchQuery` (optional): Filter on a particular property. The possible operations depend on the type of the property.

Example query:

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
from ontology_sdk.ontology.objects import ExampleRestaurant

num_example_restaurant = (
    client.ontology.objects.ExampleRestaurant
    .where(~ExampleRestaurant.object_type.restaurant_name.is_null())
    .group_by(ExampleRestaurant.object_type.restaurant_name.exact())
    .count()
    .compute()
)
```

Example API response:

```
{
    excludedItems: 0,
    data: [{
        group: {
            "restaurantName": "Restaurant Name"
        },
        metrics: [
            {
                name: "count",
                value: 100
            }
        ]
    }]
}
```

### Types of aggregations (`Aggregation`)

The following aggregation functions are available:

- [Approximate distinct](#approximate-distinct)
- [Count](#count)
- [Numeric aggregations](#numeric-aggregations)

#### Approximate distinct

Computes an approximate number of distinct values for `restaurantName`.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- name `string` (optional): Alias for the computed count. By default, this is `distinctCount`.

Example query:

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
from ontology_sdk.ontology.objects import ExampleRestaurant

num_distinct_restaurant_names = (
    client.ontology.objects.ExampleRestaurant
    .approximate_distinct(ExampleRestaurant.object_type.restaurant_name)
    .compute()
)

num_distinct_restaurant_names = (
    # This is equivalent to the previous example, but uses metric_name
    # as the name instead of the default distinctCount.
    client.ontology.objects.ExampleRestaurant
    .aggregate(
        {"metric_name": ExampleRestaurant.object_type.restaurant_name.approximate_distinct()}
    )
    .compute()
)
```

Example API response:

```
{
    excludedItems: 0,
    data: [{
        group: {},
        metrics: [
            {
                name: "distinctCount",
                value: 100
            }
        ]
    }]
}
```

#### Count

Computes the total count of `Example Restaurant` objects.

Parameters:

- name `string` (optional): Alias for the computed count. By default, this is `count`.

Example query:

```
Copied!

1
2
3
4
5
num_example_restaurant = (
    client.ontology.objects.ExampleRestaurant
    .count()
    .compute()
)
```

Example API response:

```
{
    excludedItems: 0,
    data: [{
        group: {},
        metrics: [
            {
                name: "count",
                value: 100
            }
        ]
    }]
}
```

#### Numeric aggregations

Only applies to numeric properties. Calculate the maximum, minimum, sum, or average of a numeric property for `Example Restaurant` objects.

Parameters:

- field `string`: Name of the property to use (for example, `numberOfReviews`).
- name `string` (optional): An alias for the computed value. By default, this is `avg`.

Aggregation types:

- Average: `avg()`
- Maximum: `max()`
- Minimum: `min()`
- Sum: `sum()`

Example query:

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
from ontology_sdk.ontology.objects import ExampleRestaurant

avg_number_of_reviews = (
    client.ontology.objects.ExampleRestaurant
    .avg(ExampleRestaurant.object_type.number_of_reviews)
    .compute()
)

avg_number_of_reviews = (
    # This is equivalent to the previous example, but uses metric_name
    # as the name instead of the default avg.
    client.ontology.objects.ExampleRestaurant
    .aggregate(
        {"metric_name": ExampleRestaurant.object_type.number_of_reviews.avg()}
    )
    .compute()
)
```

Example API response:

```
{
    excludedItems: 0,
    data: [{
        group: {},
        metrics: [
            {
                name: "avg",
                value: 100
            }
        ]
    }]
}
```

### Types of group bys (`GroupBy`)

You can group aggregation results by the exact values of a property.

#### Exact grouping

Groups `Example Restaurant` objects by exact values of `restaurantName`.

Parameters:

- field `string`: Name of the property to use (for example, `restaurantName`).
- maxGroupCount `integer` (optional): Maximum number of groupings of `restaurantName` to create. If using the SDK, pass this to the `exact` method.

Example query:

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
from ontology_sdk.ontology.objects import ExampleRestaurant

num_example_restaurant = (
    client.ontology.objects.ExampleRestaurant
    .group_by(ExampleRestaurant.object_type.restaurant_name.exact())
    .count()
    .compute()
)
```

Example API response:

```
{
    excludedItems: 0,
    data: [{
        group: {
            "restaurantName": "Restaurant Name"
        },
        metrics: [
            {
                name: "count",
                value: 100
            }
        ]
    }]
}
```

#### Group by linked object properties

For a many-to-one relationship, use `with_properties` to materialize the linked object's derived property. Then, group by the materialized property.

The following example sums the number of reviews for Example Restaurants, grouped by a property on a linked object. Replace `linked_object` and `linked_property` with the link and property names from your own Ontology:

Derived properties are a beta feature. To use `with_properties` and the `derived` accessor, run your code inside the `AllowBetaFeatures` context described in [Beta features](/docs/foundry/ontology-sdk/python-osdk-migration/#beta-features).

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
from ontology_sdk.ontology.objects import ExampleRestaurant

restaurants = client.ontology.objects.ExampleRestaurant

result = (
    restaurants.with_properties(
        linked_property=
            ExampleRestaurant.object_type.derived.linked_object().linked_property.get()
    )
    .where(
        ~ExampleRestaurant.object_type.derived.property("linked_property").is_null()
    )
    .group_by(
        ExampleRestaurant.object_type.derived.property("linked_property").exact()
    )
    .aggregate({"sum": ExampleRestaurant.object_type.number_of_reviews.sum()})
    .compute()
    .to_dict()
)
```

Key steps:

1. Use `with_properties` to add a property by pulling it from a linked object via the derived accessor.
2. Filter out rows where the property is null.
3. Use the property as the grouping key in `group_by`, then aggregate.

## Actions on the Ontology

Apply an [Action](/docs/foundry/action-types/overview/) through `client.ontology.actions`. The examples in this section use placeholder names such as `action_example` and `parameter_example`; substitute the API names from your own Ontology.

`ActionConfig` controls whether the Action is validated, executed, or both, and whether the response carries the resulting edits. Check `response.validation.result` before relying on the edits.

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
39
40
from foundry_sdk_runtime.types import (
    ActionConfig,
    ActionMode,
    ReturnEditsMode,
    SyncApplyActionResponse
)

response: SyncApplyActionResponse = client.ontology.actions.action_example(
    action_config=ActionConfig(
        mode=ActionMode.VALIDATE_AND_EXECUTE,
        return_edits=ReturnEditsMode.ALL),
    parameter_example="value"
)
if response.validation.result == "VALID":
    ...

print(response)
# Example output:
# SyncApplyActionResponse(
#     validation=ValidateActionResponse(
#         result='VALID',
#         submission_criteria=[],
#         parameters={}
#     ),
#     edits=ObjectEdits(
#         type='edits',
#         edits=[
#             AddObject(
#                 primary_key='value',
#                 object_type='ExampleObjectType',
#                 type='addObject'
#             )
#         ],
#         added_object_count=1,
#         modified_objects_count=0,
#         deleted_objects_count=0,
#         added_links_count=0,
#         deleted_links_count=0
#     )
# )
```

`ValidationResult` is a `Literal`, so compare it to the string `"VALID"` as shown above. Import it from `foundry_sdk.v2.ontologies.models`.

### Apply batch action

Apply the same Action to several parameter sets in one call through `client.ontology.batch_actions`. Batch application is all-or-nothing: if any Action in the batch fails, none of the edits in the batch are applied.

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
39
from foundry_sdk_runtime.types import BatchActionConfig, BatchApplyActionResponse, ReturnEditsMode
from ontology_sdk.ontology.action_types import ActionExampleBatchRequest

response: BatchApplyActionResponse = client.ontology.batch_actions.action_example(
    batch_action_config=BatchActionConfig(return_edits=ReturnEditsMode.ALL),
    requests=[
        ActionExampleBatchRequest(
            parameter_example="value_1"
        ),
        ActionExampleBatchRequest(
            parameter_example="value_2"
        )
    ]
)

print(response)
# Example output:
# BatchApplyActionResponse(
#     edits=BatchActionObjectEdits(
#         type='edits',
#         edits=[
#             AddObject(
#                 primary_key='value_1',
#                 object_type='ExampleObjectType',
#                 type='addObject'
#             ),
#             AddObject(
#                 primary_key='value_2',
#                 object_type='ExampleObjectType',
#                 type='addObject'
#             )
#         ],
#         added_object_count=2,
#         modified_objects_count=0,
#         deleted_objects_count=0,
#         added_links_count=0,
#         deleted_links_count=0
#     )
# )
```

## Queries

Execute a published [function](/docs/foundry/functions/overview/) through `client.ontology.queries`. All arguments are keyword arguments. The return type depends on what the function declares, and each of the shapes below is returned as an instance rather than as a raw identifier.

### Object output

```
Copied!

1
2
3
4
5
result: Object = client.ontology.queries.query_object_output(object_id = 1)

print(result)
# Example output:
# Object(id = 1)
```

### Object set output

```
Copied!

1
2
3
4
5
result: ObjectSet = client.ontology.queries.query_object_set_output(object_id = 1)

print(result)
# Example output:
# ObjectSet(object_set_definition=...)
```

### Attachment output

```
Copied!

1
2
3
4
5
result: Attachment = client.ontology.queries.query_attachment_output(object_id = 1)

print(result)
# Example output:
# Attachment(rid="ri.attachments.main.attachment.2c1432f8-0378-45f2-8280-55d858cb71fc")
```

### Two-dimensional aggregation output

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
result: QueryTwoDimensionalAggregation = client.ontology.queries.query_two_dimensional_output()

print(result)
# Example output:
# QueryTwoDimensionalAggregation(
#     groups=[
#         QueryAggregation(key='1', value=2.0),
#         QueryAggregation(key='2', value=3.0)
#     ]
# )
```

### Three-dimensional aggregation output

The inner buckets are held under a `groups` key at both levels.

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
result: QueryThreeDimensionalAggregation = client.ontology.queries.query_three_dimensional_output()

print(result)
# Example output:
# QueryThreeDimensionalAggregation(
#     groups=[
#         NestedQueryAggregation(
#             key='1',
#             groups=[
#                 QueryAggregation(key='ABC', value=2.0),
#                 QueryAggregation(key='DEF', value=4.0)
#             ]
#         ),
#         NestedQueryAggregation(
#             key='2',
#             groups=[
#                 QueryAggregation(key='GHI', value=1.0),
#                 QueryAggregation(key='JKL', value=1.0)
#             ]
#         )
#     ]
# )
```

### Map output

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
result: dict[Object, int] = client.ontology.queries.query_map_output(object_id = 1)

print(result)
# Example output:
# {
#     Object(id=1): 1,
#     Object(id=2): 3
# }
```

## Attachments

Upload a file as an attachment through `client.ontology.attachments`. The returned `Attachment` exposes `get_metadata()` for the file metadata and `read()` for the content.

Uploaded attachments are temporary

An attachment that has not been written to an object property by an applied action within one hour of upload is deleted, along with its content. To retain an attachment, pass the returned RID to an action that writes it to an attachment property. Until the attachment is written to an object, only the user who uploaded it can read it.

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
result: Attachment = client.ontology.attachments.upload(
    file_path="file.json",
    attachment_name="myFile"
)
print(result)
# Example output:
# Attachment(rid=ri.attachments.main.attachment.00000000-0000-0000-0000-000000000000)

print(result.get_metadata())
# Example output:
# AttachmentMetadata(
#     rid='ri.attachments.main.attachment.00000000-0000-0000-0000-000000000000',
#     filename='myFile',
#     size_bytes=20,
#     media_type='*/*',
#     type='single'
# )

print(result.read().getvalue())
# Example output:
# b'My example file.\n'
```

## Object property types

Markings and vector properties are beta features. Read them inside the `AllowBetaFeatures` context described in [Beta features](/docs/foundry/ontology-sdk/python-osdk-migration/#beta-features). Media properties are read directly, as shown below.

### Markings

A marking is a string representing a mandatory security control that restricts resource access to users who satisfy every applied marking criterion.

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
from foundry_sdk_runtime import AllowBetaFeatures
from foundry_sdk_runtime.markings import Marking

my_object_set = client.ontology.objects.Object

with AllowBetaFeatures():
    my_object = my_object_set.get(1)

    print(my_object.marking_property)
# Example output:
# Marking("MyMarking")
```

### Media

A media property gives you the content, the metadata, and the media reference.

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
from foundry_sdk_runtime.media import Media

my_object_set = client.ontology.objects.Object

my_object = my_object_set.get(1)

print(my_object.media_property)
# Example output:
# Media("media_property")

print(my_object.media_property.get_media_content())
# Example output:
# <_io.BytesIO object at 0x106ed32c0>

print(my_object.media_property.get_media_metadata())
# Example output:
# MediaMetadata(
#     path='my_file.png'
#     size_bytes=1408
#     media_type='image/png'
# )

print(my_object.media_property.get_media_reference())
# Example output:
# MediaReference(
#     mime_type='image/png'
#     reference=MediaSetViewItemWrapper(
#         media_set_view_item=MediaSetViewItem(
#             media_set_rid='ri.mio.main.media-set.00000000-0000-0000-0000-000000000000',
#             media_set_view_rid='ri.mio.main.view.00000000-0000-0000-0000-000000000000',
#             media_item_rid='ri.mio.main.media-item.00000000-0000-0000-0000-000000000000',
#             token=None
#         ),
#         type='mediaSetViewItem'
#     )
# )
```

### Vector

Vector properties are lists of floats, and can be used for nearest neighbor filtering.

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
from foundry_sdk_runtime import AllowBetaFeatures
from foundry_sdk_runtime.vectors import Vector
from ontology_sdk.ontology.objects import Object

my_object_set = client.ontology.objects.Object

with AllowBetaFeatures():
    my_object = my_object_set.get(
        1,
        properties=[Object.object_type.vector_property]
    )

    print(my_object.vector_property)
# Example output:
# Vector([0.014408838003873825, ..., -0.012173213064670563])
```

## Use a scenario-scoped client

Experimental

Ontology scenario support is in the [experimental](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment. Functionality may change during active development.

OSDK can also be used to create and interact with [Ontology scenarios](/docs/foundry/ontology/overview-ontology-scenario/). You can use scenarios to build what-if analyses without changing the main Ontology. Create a scenario-scoped client or attach to an existing scenario by its RID. Foundry evaluates calls from this client against the scenario's isolated sandbox.

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
from foundry_sdk_runtime import AllowBetaFeatures
from osdk import ScenarioClient

with AllowBetaFeatures():
    # Create a new scenario. This calls the platform to mint the scenario.
    scenario_client = ScenarioClient.create(client)

    # Or attach to an existing scenario by RID.
    scenario_client = ScenarioClient(
        client, scenario_rid="ri.actions..scenario.0000-..."
    )

    result = scenario_client.ontology.objects.ExampleRestaurant.get("primaryKey")
```

To explore additional capabilities of the scenario-scoped client and check version requirements, review [Use scenarios with OSDK](/docs/foundry/ontology/osdk-scenario/).
