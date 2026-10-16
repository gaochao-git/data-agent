# Palantir Foundry Docs — /docs/foundry/functions/python-staged-writes

> 来源: https://www.palantir.com/docs/foundry/functions/python-staged-writes/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Staged writes in Python [Beta]

Beta

Staged writes are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development. Functionality may change during active development.

Live preview and published function preview are only supported in Code Repositories, not in local development or VS Code workspace environments.

Staged writes provide an additional execution model for Python functions that edit objects in the Ontology. Unlike regular [Python Ontology edit functions](/docs/foundry/functions/python-ontology-edits/), staged-write functions:

  * Provide a read-after-write guarantee for Ontology edits applied in the function. All edits applied within the function are staged and reflected in Ontology queries and aggregations later in the function.
  * Allow nested calls to other staged-write functions that make Ontology edits.



This page shows how to write staged-write functions in Python and documents their unique properties. For TypeScript v2, see [staged writes in TypeScript v2 functions](/docs/foundry/functions/typescript-v2-staged-writes/). For more details about how edit functions work, refer to the [Ontology edits overview](/docs/foundry/functions/edits-overview/).

## Key differences from regular Ontology edit functions

Staged-write functions differ from regular edit functions in several important ways.

### Read-after-write guarantee

Within a staged-write function, any Ontology data read reflects all edits previously made in the function and all edits made by a calling staged-write function within the same action execution. These edits are staged only and are not visible to other users or functions outside the execution context. You can query the Ontology from within the function using search requests and aggregations, and the results will reflect all previously staged edits.

### No requirement to return edits

Regular Python Ontology edit functions must return a batch of Ontology edits for those edits to be applied. Staged-write functions automatically stage their edits and apply them to the Ontology when the action completes. The function can return any supported function type instead of returning edits.

### Atomic execution

All operations within a staged-write function, including queries, function calls, and AIP Logic executions, stage their edits together. The edits are committed to the Ontology after the function completes successfully. If the function raises an error, the Ontology remains unmodified, and all staged edits are discarded before the action retries the function.

### `WriteableClient`

Python staged-write functions use a generated `WriteableClient` instead of the standard `FoundryClient`. Use the `WriteableClient` to construct a staged-writes container. The container provides the same object and link edit APIs as a regular Python edits container, but you do not return its edits from the function.

## Define a staged-write function

To define a Python staged-write function:

  * Set `beta=True` in the `@function` decorator.
  * Use the decorator's `edits` parameter to declare every object and link type that the function can edit, including through nested function calls. To declare a link edit, use `OntologyLinkType(SourceObjectType, "linkApiName")`.
  * Annotate the first parameter with the generated `WriteableClient` type. The functions runtime injects a client with the transaction context for the action execution, so do not construct the client in the function.
  * Return any supported function type.



The following function declares and applies an edit to the `Employee` object type:
    
    
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
    from functions.api import Integer, function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Employee
    
    @function(beta=True, edits=[Employee])
    def update_employee_last_name(
        client: WriteableClient,
        employee_id: Integer,
        new_last_name: str,
    ) -> Integer:
        staged_writes = client.ontology.staged_writes()
        editable_employee = staged_writes.objects.Employee.edit(employee_id)
        editable_employee.last_name = new_last_name
        return employee_id

Configure a staged-write function as a [function-backed action](/docs/foundry/action-types/function-actions-overview/) to apply its edits.

## Create objects

Use the object type's `create` method on the staged-writes container. You must provide the object's primary key and can initialize other properties in the same call:
    
    
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
    from functions.api import function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Employee
    
    @function(beta=True, edits=[Employee])
    def create_employee(
        client: WriteableClient,
        employee_id: str,
        first_name: str,
        last_name: str,
    ) -> str:
        staged_writes = client.ontology.staged_writes()
        staged_writes.objects.Employee.create(
            employee_id=employee_id,
            first_name=first_name,
            last_name=last_name,
        )
        return employee_id

### Create objects with generated IDs

You can generate a primary key, create the object, and return or use the key later in the same execution:
    
    
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
    from uuid import uuid4
    
    from functions.api import function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Ticket
    
    @function(beta=True, edits=[Ticket])
    def create_ticket(client: WriteableClient, title: str) -> str:
        ticket_id = str(uuid4())
        staged_writes = client.ontology.staged_writes()
        staged_writes.objects.Ticket.create(
            ticket_id=ticket_id,
            title=title,
            status="open",
        )
        return ticket_id

## Update objects

Use the object type's `edit` method on the staged-writes container. You can pass an object or its primary key, then assign new property values to the editable object:
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    staged_writes = client.ontology.staged_writes()
    editable_employee = staged_writes.objects.Employee.edit(employee)
    editable_employee.last_name = new_name
    
    editable_employee = staged_writes.objects.Employee.edit(23)
    editable_employee.last_name = new_name

Interface edits are not supported in staged-write functions.

## Delete objects

Call the object type's `delete` method with an object or its primary key:
    
    
    Copied!
    
    1
    2
    3
    staged_writes = client.ontology.staged_writes()
    staged_writes.objects.Ticket.delete(ticket)
    staged_writes.objects.Ticket.delete(12)

## Create or delete links

Edit an object through the staged-writes container, then use the link property's `add` or `remove` method to edit many-to-many links:
    
    
    Copied!
    
    1
    2
    3
    4
    staged_writes = client.ontology.staged_writes()
    editable_employee = staged_writes.objects.Employee.edit(employee)
    editable_employee.assigned_tickets.add(ticket)
    editable_employee.assigned_tickets.remove(ticket)

To edit one-to-many links, edit the foreign key property using a create or update object edit.

## Read-after-write within a staged-write function

Reads through the `WriteableClient` reflect data written earlier in the same execution. Before an Ontology read, the client sends pending edits to the staged transaction so the query or aggregation includes them.

The following function creates a ticket, then counts open tickets. The result includes the newly created ticket:
    
    
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
    from functions.api import Integer, function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Ticket
    
    @function(beta=True, edits=[Ticket])
    def create_ticket_and_count_open(
        client: WriteableClient,
        ticket_id: str,
        title: str,
    ) -> Integer:
        staged_writes = client.ontology.staged_writes()
        staged_writes.objects.Ticket.create(
            ticket_id=ticket_id,
            title=title,
            status="open",
        )
        return (
            client.ontology.objects.Ticket.where(
                Ticket.object_type.status == "open"
            )
            .count()
            .compute()
        )

## Call other functions from a staged-write function

When you call another function or query through the `WriteableClient`, those operations participate in the same staged edits. Reads in the called function reflect edits previously staged in the execution, and edits made by the called function join the same staged edits. This applies to:

  * Other Python or TypeScript v2 staged-write functions
  * AIP Logic functions
  * Ontology queries



If the top-level function completes successfully, all edits staged across the nested calls are committed together. If any call raises an error, the entire set of edits is discarded.

In the following example, `assign_ticket` is a separate staged-write function published from the same repository. `bulk_assign_tickets` calls it through the OSDK-generated query API, and each invocation adds its edits to the same staged edits:
    
    
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
    from functions.api import Integer, OntologyLinkType, function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Employee, Ticket
    
    @function(
        beta=True,
        edits=[Ticket, OntologyLinkType(Employee, "assignedTickets")],
    )
    def bulk_assign_tickets(
        client: WriteableClient,
        employee_id: Integer,
        ticket_ids: list[str],
    ) -> Integer:
        for ticket_id in ticket_ids:
            client.ontology.queries.assign_ticket(
                employee_id=employee_id,
                ticket_id=ticket_id,
            )
        return len(ticket_ids)

## Execution lifecycle

Staged-write functions use the following execution lifecycle:

  1. **Function execution:** Creates, updates, deletes, reads, and nested function calls participate in the same staged transaction. The edits are visible to the function and nested functions but do not appear outside the execution until committed.
  2. **Commit:** If the function completes successfully, all staged edits are committed before the action finishes.
  3. **Rollback on error:** If the function raises an error, the Ontology remains unmodified, and all staged edits are discarded. The action can then retry the function.


    
    
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
    from functions.api import Integer, function
    from ontology_sdk import WriteableClient
    from ontology_sdk.ontology.objects import Employee
    
    @function(beta=True, edits=[Employee])
    def update_employee_with_validation(
        client: WriteableClient,
        employee_id: Integer,
        new_salary: float,
    ) -> Integer:
        if new_salary < 0:
            raise ValueError("Salary cannot be negative")
        staged_writes = client.ontology.staged_writes()
        editable_employee = staged_writes.objects.Employee.edit(employee_id)
        editable_employee.salary = new_salary
        return employee_id

[âPREVIOUSOntology edits](/docs/foundry/functions/python-ontology-edits/)

[NEXTUse Python functions in Pipeline Builderâ](/docs/foundry/functions/python-functions-builder/)