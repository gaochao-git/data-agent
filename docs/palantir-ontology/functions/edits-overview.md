# Palantir Foundry Docs — /docs/foundry/functions/edits-overview

> 来源: https://www.palantir.com/docs/foundry/functions/edits-overview/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Ontology edits

An **Ontology edit** is the act of creating, modifying, or deleting an object. Functions support returning [Ontology edits](/docs/foundry/functions/types-reference/#ontology-edit) for use in a [function-backed action](/docs/foundry/action-types/function-actions-overview/).

  * TypeScript v1 functions are authored using the `@OntologyEditFunction` decorator, which provides special semantics to simplify your code. TypeScript v1 functions also use the [`@Edits` decorator](/docs/foundry/functions/api-ontology-edits/#the-edits-decorator) to provide actions with provenance information, which the actions may use to [enforce permissions](/docs/foundry/action-types/permissions/). You can write unit tests for TypeScript v1 Ontology edit functions using the APIs available for [verifying Ontology edits](/docs/foundry/functions/unit-test-ontology-edits/).
  * TypeScript v2 functions are authored using the [`createEditBatch`](/docs/foundry/functions/typescript-v2-ontology-edits/#construct-an-ontology-edits-batch) function exported from the `@osdk/functions` package. These functions rely on the `Edits` type to provide actions with provenance information.
  * Python functions are authored by creating an edits container using the [`FoundryClient`](/docs/foundry/functions/python-ontology-edits/#construct-an-ontology-edits-container) exported from the Ontology SDK. These functions rely on the `edits` parameter of the [`@function`](/docs/foundry/functions/python-ontology-edits/#define-an-edit-function) decorator to provide actions with provenance information.



### Link type edits

Adding or removing a link between two objects is an Ontology edit, just like creating an object or changing a property value. For complete [provenance](/docs/foundry/action-types/function-actions-getting-started/#provenance), declare many-to-many link edits or, for one-to-one and one-to-many links, edits to the object type holding the foreign key.

In the following example, `assignedTickets` is a many-to-many link type from the `Employee` object type to the `Ticket` object type. The function adds a link to one ticket and removes a link to another.

TypeScript v1TypeScript v2Python
    
    
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
    import { Edits, OntologyEditFunction } from "@foundry/functions-api";
    import { Employee, Ticket } from "@foundry/ontology-api";
    
    export class TicketLinkFunctions {
        @Edits(Employee, Ticket)
        @OntologyEditFunction()
        public reassignTicket(employee: Employee, ticketToAssign: Ticket, ticketToUnassign: Ticket): void {
            employee.assignedTickets.add(ticketToAssign);
            employee.assignedTickets.remove(ticketToUnassign);
        }
    }
    
    
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
    import { Employee, Ticket } from "@ontology/sdk";
    import { Client, Osdk } from "@osdk/client";
    import { createEditBatch, Edits } from "@osdk/functions";
    
    type OntologyEdit = Edits.Link<Employee, "assignedTickets">;
    
    export default function reassignTicket(
        client: Client,
        employee: Osdk.Instance<Employee>,
        ticketToAssign: Osdk.Instance<Ticket>,
        ticketToUnassign: Osdk.Instance<Ticket>,
    ): OntologyEdit[] {
        const batch = createEditBatch<OntologyEdit>(client);
    
        batch.link(employee, "assignedTickets", ticketToAssign);
        batch.unlink(employee, "assignedTickets", ticketToUnassign);
    
        return batch.getEdits();
    }
    
    
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
    from functions.api import OntologyEdit, OntologyLinkType, function
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import Employee, Ticket
    
    @function(edits=[OntologyLinkType(Employee, "assignedTickets")])
    def reassign_ticket(
        employee: Employee,
        ticket_to_assign: Ticket,
        ticket_to_unassign: Ticket,
    ) -> list[OntologyEdit]:
        ontology_edits = FoundryClient().ontology.edits()
    
        editable_employee = ontology_edits.objects.Employee.edit(employee)
        editable_employee.assigned_tickets.add(ticket_to_assign)
        editable_employee.assigned_tickets.remove(ticket_to_unassign)
    
        return ontology_edits.get_edits()

The rest of this document describes how Ontology edit functions work behind the scenes to provide you with a better understanding of the underlying infrastructure.

### When edits are applied

A common misunderstanding about Ontology edit functions is whether or not running them will update objects in the Ontology. When you run an Ontology edit function in the functions helper in **Authoring** , edits are not applied to the actual objects. The only way to update objects using a function is by configuring an action to use the function as described in the documentation for [function-backed actions](/docs/foundry/action-types/function-actions-overview/).

This means that you can freely run Ontology edit functions in the functions helper to validate results on various inputs, without concern that the objects themselves will be updated.

### Caveats

#### Edits and object search

Changes to objects and links are propagated to the object set APIs _after_ your function has finished executing. This means that any `Objects.search()` query whose results depend on an edited value, such as a filter, search around, or aggregation on the property or link you edited, will use the old objects, properties, and links, and may not reflect the edits to the Ontology, including creation and deletion. Your function will need to handle this case manually.

This is distinct from retrieving a specific object that you have already edited, for example by its primary key. In that case, the functions infrastructure applies your pending edits when the object is materialized, so the edited property and link values are returned. See [The @Edits decorator](/docs/foundry/functions/api-ontology-edits/#the-edits-decorator) for an example.

For the following example, assume there is an Employee with ID 1.

TypeScript v1TypeScript v2Python
    
    
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
    import { OntologyEditFunction, Edits } from "@foundry/functions-api";
    import { Employee, Objects } from "@foundry/ontology-api";
    
    export class CaveatEditFunctions {
        @Edits(Employee)
        @OntologyEditFunction()
        public async editAndSearch(): Promise<void> {
            const employeeOne = Objects.search().employee().filter(e => e.id.exactMatch(1)).all()[0];
            employeeOne.name = "Bob";
    
            const count = await Objects.search().employee().filter(e => e.name.exactMatch("Bob")).count() ?? -1;
            console.log(count);
            // Expected: 1, Actual: 0
        }
    }
    
    
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
    import { Client } from "@osdk/client";
    import { Employee } from "@ontology/sdk";
    import { Edits, createEditBatch } from "@osdk/functions";
    
    type OntologyEdit = Edits.Object<Employee>;
    
    async function editAndSearch(client: Client): Promise<OntologyEdit[]> {
        const batch = createEditBatch<OntologyEdit>(client);
    
        const employeeOne = await client(Employee).fetchOne(1);
        batch.update(employeeOne, { name: "Bob" });
    
        const count = await client(Employee)
            .where({
                name: {
                    $eq: "Bob"
                }
            })
            .aggregate({
                $select: {
                    $count: "unordered"
                }
            })
            .then(response => response.$count);
        console.log(count);
        // Expected: 1, Actual: 0
    
        return batch.getEdits();
    }
    
    export default editAndSearch;
    
    
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
    from functions.api import function, OntologyEdit
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import Employee
    
    @function(edits=[Employee])
    def edit_and_search() -> list[OntologyEdit]:
        client = FoundryClient()
        ontology_edits = client.ontology.edits()
    
        employee = client.ontology.objects.Employee.get(1)
        editable_employee = ontology_edits.objects.Employee.edit(employee)
        editable_employee.name = "Bob"
    
        count = client.ontology.objects.Employee.where(Employee.object_type.name == "Bob").count().compute()
        print(count)
        # Expected: 1, Actual: 0
    
        return ontology_edits.get_edits()

#### Apply Ontology edits in Automate

[Automate](/docs/foundry/automate/overview/) does not apply edits returned by functions used as effects. To apply Ontology edits through an automation, configure a function-backed action instead. For more information, see [Function effects](/docs/foundry/automate/effect-function/).

#### Optional arrays in function-backed actions

While omitted optional arrays are handled as `undefined` when running an `@OntologyEditFunction` in code repositories, they are passed as empty arrays when executing the function through an action.

[âPREVIOUSFunction consumption / Version range dependencies](/docs/foundry/functions/version-range-dependencies-for-functions/)

[NEXTPublish and call query functions through API gatewayâ](/docs/foundry/functions/query-functions/)