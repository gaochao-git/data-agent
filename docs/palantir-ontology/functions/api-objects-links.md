# Palantir Foundry Docs — /docs/foundry/functions/api-objects-links

> 来源: https://www.palantir.com/docs/foundry/functions/api-objects-links/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# API: Objects and links

This page covers TSv1. TSv2 and Python read objects through the Ontology SDK: see [Object APIs by language](/docs/foundry/functions/functions-on-objects/#object-apis-by-language), or [Links](/docs/foundry/ontology-sdk/typescript-osdk/#links) in the TypeScript OSDK reference for the TSv2 traversal APIs.

Every [object type](/docs/foundry/object-link-types/object-types-overview/) imported into your project is converted to a TypeScript v1 API so you can easily access and manipulate objects available in Foundry.

### Properties

[Properties](/docs/foundry/object-link-types/properties-overview/) of each object type are converted to fields on the TypeScript interface generated for each object type. The generated field name uses the API Name specified in the ontology.

You can access the fields for each property with simple dot notation:

TypeScript v1
    
    
    Copied!
    
    1
    const firstName = employee.firstName;

Because properties may not have a concrete value set, the returned type when accessing a property value could be `undefined`. In TypeScript, the compiler will give an error unless you explicitly handle the `undefined` case. See [this guide](/docs/foundry/functions/undefined-values/) for more details on this.

#### Array properties

In TypeScript v1, array properties on an object type are converted to `ReadonlyArray` types. This is so that the semantics for [editing](/docs/foundry/functions/api-ontology-edits/) an array property are clearâthe only way to modify the values of an array property is to update it with an entirely new array value.

Because of this, copy an array property before you manipulate its values:

TypeScript v1
    
    
    Copied!
    
    1
    2
    3
    4
    // Copy to a new array
    let arrayCopy = [...myObject.myArrayProperty];
    // Now you can modify the copied array
    arrayCopy.push(newItem);

TypeScript v2 types an array property as a plain `T[]` rather than a `ReadonlyArray`, so the compiler permits modifying it in place. An edit still replaces the whole array value, so build a new array and assign it to the property in every version. Python behaves the same way; see [Ontology edits](/docs/foundry/functions/python-ontology-edits/).

### Link types

[Link types](/docs/foundry/object-link-types/link-types-overview/) between object types are also converted to top-level fields on the TypeScript interface for each object type. To traverse the link, access the field and then call one of the methods used to load the objects. Link type field names are generated using the API Name specified in the ontology.

The Foundry Ontology supports defining 1-to-1, 1-to-many, and many-to-many link types. When accessing the `1` side of a link, the generated field is of the `SingleLink` type, and you can access the linked object using the `get()` or `getAsync()` methods:

TypeScript v1
    
    
    Copied!
    
    1
    const manager = employee.manager.get();

When you traverse a 1-to-1 or many-to-1 link, the return value may be `undefined` if there is no linked object; follow the [guide](/docs/foundry/functions/undefined-values/) for handling `undefined` values for these links.

When accessing the `many` side of a link, the generated field is of the `MultiLink` type. You can access an Array of linked objects using the `all()` or `allAsync()` methods. If there are no linked objects, these methods will return an empty Array:

TypeScript v1
    
    
    Copied!
    
    1
    const employees = employee.reports.all();

Traversing links can be expensive because it requires loading which objects are linked in the backend. For details about how to perform link traversals more efficiently, see [this section](/docs/foundry/functions/optimize-performance/#optimizing-link-traversals).

The array of linked objects returned from calling `.all()` or `.allAsync()` is a `ReadonlyArray`. If you want to modify the array, make a copy of it first:

TypeScript v1
    
    
    Copied!
    
    1
    let copiedEmployees = [...employee.reports.all()];

You can traverse links as an `ObjectSet` to avoid loading linked object instances in the memory. When links are created in the Ontology, APIs will be generated on an object set of this type to "search around" to other linked object sets.

TypeScript v1
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    7
    import { ObjectSet, Employee, OtherObjectType } from "@foundry/ontology-api";
    
    // Assume you have an object set available:
    // const employee_id = "123";
    // const employeeObjectSet : ObjectSet<Employee> = Objects.search().employee().filter(exactMatch(employee_id));
    
    const linkedObjs: ObjectSet<OtherObjectType> = employeeObjectSet.searchAroundToOtherObjectType();

If you operate on a single instance of an object and search around from there, you will get a `MultiLink<objectType>`. You cannot convert this `MultiLink` to an `ObjectSet`; you must convert the object instance to an object set to pivot to other object sets.

TypeScript v1
    
    
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
    // Assuming:
    // const employee: Employee
    
    // MultiLink can be loaded in memory to process further.
    
    const linkedObjs: MultiLink<objectType> = employee.reports
    
    // Convert a sole object instance to an object set. This statement will take longer than an `employee().filter()` statement.
    const employeeObjectSet : ObjectSet<Employee> = Objects.search().employee([employee])
    
    // From there, you can use the above "searchAroundToOtherObjectType" to process only object sets.

For the TSv2 form, see [Links](/docs/foundry/ontology-sdk/typescript-osdk/#links) in the TypeScript OSDK reference, and the [link traversal mapping](/docs/foundry/functions/typescript-v2-migration/#link-traversal-mapping) for the method-by-method conversion. For a mapping of each TSv1 identifier and type to its TSv2 equivalent, review the [identifier reference](/docs/foundry/functions/typescript-v2-migration/#identifier-reference).

### Ontology metadata

Functions provides access to the available Ontology by providing the list of objects and properties. Ontology metadata information is available by accessing the constant types of each object type. See the sections below for more details.

These Ontology metadata APIs are TSv1 only, with no TSv2 or Python equivalent.

#### Object property metadata

Object properties also include type metadata, which provides programmatic access to the type of each property. You can use this functionality for advanced workflows like identifying all properties of a given type or validating that a given property name has a specific type.

For instance, for an Ontology that contains an employee object **type** , you can access the type information on that object **type 's** property as follows:

TypeScript v1
    
    
    Copied!
    
    1
    2
    3
    import { Employee } from "@foundry/ontology-api";
    ...
    const type = Employee.properties.firstName;

In this case, if `firstName` is a string property on the `Employee` object type, then its type will be a `StringPropertyBaseType`.

The following property types are available:

  * `BooleanPropertyBaseType`
  * `BytePropertyBaseType`
  * `DatePropertyBaseType`
  * `FloatPropertyBaseType`
  * `TimestampPropertyBaseType`
  * `ShortPropertyBaseType`
  * `GeohashPropertyBaseType` (To be used with `geopoint` properties, previously named `geohash` properties.)
  * `DecimalPropertyBaseType`
  * `StringPropertyBaseType`
  * `LongPropertyBaseType`
  * `IntegerPropertyBaseType`
  * `DoublePropertyBaseType`
  * `ArrayPropertyBaseType`
  * `VectorPropertyBaseType`



[âPREVIOUSImport object, interface, and link types](/docs/foundry/functions/ontology-imports/)

[NEXTObject sets (TSv1)â](/docs/foundry/functions/api-object-sets/)