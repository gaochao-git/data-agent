# Palantir Foundry Docs — /docs/foundry/functions/object-identifiers

> 来源: https://www.palantir.com/docs/foundry/functions/object-identifiers/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Object identifiers

This page covers TSv1, which identifies objects with `typeId` and `primaryKey`. For the TSv2 fields and how each maps, see [Identify objects](/docs/foundry/functions/typescript-v2-migration/#identify-objects).

The identity of an object in Foundry is represented in a few different ways, and understanding these different representations can be important for writing correct code in functions. This section explains the various ways that objects are identified and the implications for your code.

## Types of identifiers

### Object RIDs

A "RID" refers to a [Resource Identifier â](https://github.com/palantir/resource-identifier), Palantirâs open-source specification used to identify an entity. Ontology objects have a RID assigned to them when they are created, either from indexing a backing dataset or as part of an Action.

In TypeScript v1 functions, every [Ontology object](/docs/foundry/functions/api-objects-links/) has a `rid` field of type `string | undefined`. The reason a RID may be undefined is that it is possible to create a new object in functions using the [object creation](/docs/foundry/functions/api-ontology-edits/#creating-objects) API. Newly created objects always have a `rid` value of `undefined`, while existing objects always have a defined `rid`.

### Primary keys

Objects can also be uniquely identified by their object type and primary key. A primary key is a unique `propertyId` and value pair. For example, an Employee object type may be uniquely identified by a `string` property called `employeeId`.

All Ontology objects always have a `typeId` and `primaryKey` field that is present, including newly created objects. This is because you are required to provide the primary key when creating a new object.

### Object specifiers

In TypeScript v1, identifying an object uniquely requires both `typeId` and `primaryKey`, because the object type and the primary key are separate fields.

## Implications for code

### Checking for equality

Within functions, each Ontology object is represented using a [JavaScript object â](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Object). It is possible for one Ontology object to be represented as multiple JavaScript objects. For example, this can happen if you load the Ontology object from an [Object search](/docs/foundry/functions/api-object-sets/) multiple times, or load an object from an Object search in addition to having it passed in as a parameter:

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
    public myFunction(employee: Employee): void {
        const employee2 = Objects.search().employee()
            .filter(e => e.id.exactMatch(employee.id))
            .all()[0];
        console.log(employee == employee2); // false
        console.log(employee === employee2); // false
        console.log(employee.id === employee2.id); // true
    }

Even though both `employee` and `employee2` refer to the same conceptual Ontology object in the above example, comparing them using the `==` and `===` operators returns `false` because the variables refer to two distinct JavaScript objects. Simply comparing the `rid` fields can be problematic because newly created objects have a `rid` of `undefined`.

As a result, the best way to compare two Ontology objects for equality is to compare the `typeId` and `primaryKey`:

TypeScript v1
    
    
    Copied!
    
    1
    2
    3
    4
    function isEqual(o1: OntologyObject, o2: OntologyObject) {
        return o1.typeId === o2.typeId
            && JSON.stringify(o1.primaryKey) == JSON.stringify(o2.primaryKey);
    }

Both values are needed because a primary key is unique only within a single object type.

### Object mappings

It can often be useful to store a mapping from an object to some value. For example, you may want to iterate through an array of objects and store values for more efficient lookup.

Because of the equality checking issues described above, you cannot use the Ontology object itself as a key in a JavaScript Map. Instead, you can use a [FunctionsMap](/docs/foundry/functions/types-reference/#collection-types) which is specifically designed to support OntologyObjects as keys.

[âPREVIOUSGetting started](/docs/foundry/functions/foo-getting-started/)

[NEXTCreate a custom aggregation (TSv1)â](/docs/foundry/functions/create-custom-aggregation/)