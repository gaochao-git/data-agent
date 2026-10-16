# Palantir Foundry Docs — /docs/foundry/functions/typescript-v2-ontology-edits

> 来源: https://www.palantir.com/docs/foundry/functions/typescript-v2-ontology-edits/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Ontology edits

In addition to writing functions that read data from the Ontology, you can also write functions that create objects and edit the properties and links between objects. This page documents the object edit APIs available to you in functions. For more details about how edit functions work, refer to the [overview page](/docs/foundry/functions/edits-overview/).

For the edits created in a function to actually be applied, Ontology edit functions _must be configured as a[function-backed Action](/docs/foundry/action-types/function-actions-overview/)_. Configuring an Action in this way allows you to provide additional metadata, configure permissions, and access the Action in various operational interfaces. As noted in the [documentation](/docs/foundry/functions/edits-overview/#when-edits-are-applied), running an edit function outside of an Action will not actually modify any object data.

Standard Ontology edits use an edit batch. TypeScript v2 also supports [staged writes](/docs/foundry/functions/typescript-v2-staged-writes/), which provide read-after-write guarantees within a function: edits are immediately visible to later queries in the same execution, and nested staged-write function calls share those edits. Use staged writes when a function must read data that it just wrote. Staged writes are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment.

Unexpected search results affect edit batches, not staged writes

When you use an edit batch, searching for objects immediately after editing them may return unexpected results. This caveat does not apply to staged writes. See the [Caveats section](/docs/foundry/functions/edits-overview/#edits-and-object-search) for details.

## Define an edit function

Functions that edit the Ontology must explicitly declare the entities that will be edited using the `Edits` type exported from the `@osdk/functions` package. The following example declares a new type representing edits to the `Employee` and `Ticket` object types, as well as a link type between `Employee` and `Ticket`. Edits to multiple entities need to be joined with the `|` operator.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    4
    import { Employee, Person, Ticket } from "@ontology/sdk";
    import { Edits } from "@osdk/functions";
    
    type OntologyEdit = Edits.Object<Employee> | Edits.Interface<Person> | Edits.Object<Ticket> | Edits.Link<Employee, "assignedTickets">;

You must then declare that the function returns an array of edits of the new type.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    export default function createNewTicketAndAssignToEmployee(): OntologyEdit[] {
        // ...
    }

## Construct an Ontology edits batch

To perform Ontology edits in a TypeScript v2 function, first construct an Ontology edits batch using the `createEditBatch` function exported from `@osdk/functions`, passing the previously declared type as a type argument:

TypeScript v2
    
    
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
    import { Employee, Ticket } from "@ontology/sdk";
    import { Client } from "@osdk/client";
    import { createEditBatch, Edits } from "@osdk/functions";
    
    type OntologyEdit = Edits.Object<Employee> | Edits.Object<Ticket> | Edits.Link<Employee, "assignedTickets">;
    
    export default function createNewTicketAndAssignToEmployee(client: Client): OntologyEdit[] {
    
        const batch = createEditBatch<OntologyEdit>(client);
        // ...
    }

This batch is used to keep track of all edits made in a function.

## Update properties

### Object properties

Use the `update` method available on the created batch to modify one or more object properties:

TypeScript v2
    
    
    Copied!
    
    1
    batch.update(employee, { lastName: newName });

If you have not loaded the `employee` object instance into memory, you can also update it by referencing the object's API name and primary key:

TypeScript v2
    
    
    Copied!
    
    1
    batch.update({ $apiName: "Employee", $primaryKey: 23 }, { lastName: newName });

Subsequent access to the `lastName` property value of `employee` later in the same function execution will _not_ reflect the changes that you make when calling `update` on the edit batch.

Sometimes, it is useful to copy all of the property values of one instance of an object type to another instance. The following example assigns the property values of `employee2` to `employee1`:

TypeScript v2
    
    
    Copied!
    
    1
    batch.update(employee1, employee2);

The primary key property value of an existing object cannot be updated.

### Interface properties

You can use the `update` method to modify the interface properties of an object through an Ontology interface. In the example below, the type of `person` is an Ontology interface, but the underlying instance is an object that implements the `Person` interface.

The `update` method takes two arguments both for object types and interfaces. For interfaces, it takes the interface that will be modified and the interface properties to be modified.

TypeScript v2
    
    
    Copied!
    
    1
    batch.update(person, { firstName: newFirstName });

An interface property that is implemented by the primary key property of the underlying object cannot be updated.

## Update links

For many-to-many links, the `link` and `unlink` methods are available on the created batch to add or remove links between objects.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    4
    5
    // Assign an employee to an office.
    batch.link(employee, "office", office);
    
    // Unassign an office from an employee.
    batch.unlink(employee, "office", office);

Declare every link type edited this way with `Edits.Link`. The first type argument is the object type you traverse from, and the second is the link type API name defined on that object type. For a complete example of adding and removing links, see the TypeScript v2 tab in [Link type edits](/docs/foundry/functions/edits-overview/#link-type-edits).

When the link type can have many targets, pass an array to `link` and `unlink` to edit several links at once. Each element of the array produces a separate link edit.

For one-to-one and one-to-many links, use the `update` method available on the created batch to modify the foreign key property of the source object. In the example below, a one-to-many link is backed by the `assignedEmployeeId` foreign key property on the `Ticket` object type, so the link edit is an object edit. In this case, you must only declare `Edits.Object<Ticket>`.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    4
    5
    // Assign a ticket to an employee.
    batch.update({ $apiName: "Ticket", $primaryKey: 13 }, { assignedEmployeeId: 52 });
    
    // Unassign a ticket.
    batch.update({ $apiName: "Ticket", $primaryKey: 13 }, { assignedEmployeeId: undefined });

As with updating properties, you can also reference either side of the link with an API name and primary key if you have not loaded a concrete instance of the object type previously.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    4
    5
    // Assign a ticket to an employee.
    batch.link({ $apiName: "Employee", $primaryKey: 23 }, "assignedTickets", { $apiName: "Ticket", $primaryKey: 12 });
    
    // Unassign a ticket from an employee.
    batch.unlink({ $apiName: "Employee", $primaryKey: 23 }, "assignedTickets", { $apiName: "Ticket", $primaryKey: 12 });

## Create objects

### Objects

You can create new objects using the `create` method on the edit batch. When creating a new object, you must specify a value for its primary key and can optionally initialize any other properties.

In this example, we create a new `Ticket` object with the given ID, set its `dueDate` property, and assign it to the given `Employee` by modifying the `assignedTickets` link. To simplify the calculation of the new value of `dueDate`, we use the `luxon` library.

TypeScript v2
    
    
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
    import { Employee, Ticket } from "@ontology/sdk";
    import { Client, Osdk } from "@osdk/client";
    import { createEditBatch, Edits, Integer } from "@osdk/functions";
    import { DateTime } from "luxon";
    
    type OntologyEdit = Edits.Object<Employee> | Edits.Object<Ticket> | Edits.Link<Employee, "assignedTickets">;
    
    export default function createNewTicketAndAssignToEmployee(
        client: Client,
        employee: Osdk.Instance<Employee>,
        ticketId: Integer,
    ): OntologyEdit[] {
        const batch = createEditBatch<OntologyEdit>(client);
    
        batch.create(Ticket, {
            ticketId,
            dueDate: DateTime.now().plus({ days: 7 }).toFormat('yyyy-MM-dd'),
        });
    
        // The new ticket does not exist in the Ontology as a concrete instance, but we can link it
        // by referencing its API name and primary key.
        batch.link(employee, "assignedTickets", { $apiName: "Ticket", $primaryKey: ticketId });
    
        return batch.getEdits();
    }

### Interfaces

You can create new object instances through interfaces by calling the `create` method and specifying an interface, underlying object type, and a set of interface properties. One of the interface properties supplied must be implemented by the primary key property of the underlying object type.

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    4
    5
    editBatch.create(Person, {
        $objectType: "Employee",
        firstName: "John",
        lastName: "Doe",
    });

## Delete objects

### Objects

You can delete an object by calling the `delete` method on the edit batch.

In this example, we delete all the tickets assigned to the given employee:

TypeScript v2
    
    
    Copied!
    
    1
    2
    3
    for await (const ticket of employee.$link.assignedTickets.asyncIter()) {
        batch.delete(ticket);
    }

Objects may also be deleted using a primary key instead of an instance:

TypeScript v2
    
    
    Copied!
    
    1
    batch.delete({ $apiName: "Ticket", $primaryKey: 12 });

### Interfaces

You can delete an object through an interface by calling the `delete` method.

TypeScript v2
    
    
    Copied!
    
    1
    batch.delete(person);

## Edits on struct properties

Ontology struct properties for both object and interface types can be edited with TypeScript v2 functions. [Struct types](/docs/foundry/functions/types-reference/#structcustom-type) in TypeScript v2 are defined using TypeScript interfaces. Struct types in functions can be used to edit Ontology struct properties, as long as they contain the same fields as the struct property, with field names matching the API names of the Ontology struct property fields.

TypeScript v2
    
    
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
    interface Address {
        street: string,
        city: string,
        state: string,
        country: string,
        zipcode: string,
    }
    
    export default function updateEmployeeAddress(
        client: Client,
        employee: Osdk.Instance<Employee>,
        newAddress: Address
    ): OntologyEdit[] {
        const batch = createEditBatch<OntologyEdit>(client);
        batch.update(employee, { address: newAddress });
        return batch.getEdits();
    }

[âPREVIOUSMigrate from TypeScript v1 to TypeScript v2](/docs/foundry/functions/typescript-v2-migration/)

[NEXTStaged writesâ](/docs/foundry/functions/typescript-v2-staged-writes/)