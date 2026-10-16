# Palantir Foundry Docs — /docs/foundry/functions/python-ontology-edits

> 来源: https://www.palantir.com/docs/foundry/functions/python-ontology-edits/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Ontology edits

In addition to writing functions that read data from the Ontology, you can also write functions that create objects and edit the properties and links between objects. This page documents the object edit APIs available to you in functions. For more details about how edit functions work, refer to the [overview page](/docs/foundry/functions/edits-overview/).

Python also supports [staged writes](/docs/foundry/functions/python-staged-writes/), which provide read-after-write guarantees and allow nested calls to other staged-write functions. Use staged writes when a function must query data that it just edited.

For the edits created in a function to actually be applied, Ontology edit functions _must be configured as a[function-backed Action](/docs/foundry/action-types/function-actions-overview/)_. Configuring an Action in this way allows you to provide additional metadata, configure permissions, and access the Action in various operational interfaces. As noted in the [documentation](/docs/foundry/functions/edits-overview/#when-edits-are-applied), running an edit function outside of an Action will not actually modify any object data.

Searching for objects immediately after editing them may return unexpected results. See the [Caveats section](/docs/foundry/functions/edits-overview/#edits-and-object-search) for details.

## Define an edit function

Functions that edit the Ontology must:

  * Be decorated with the `@function(edits=[MyObjectType])` decorator imported from `functions.api` to specify the object types that will be edited.
  * Declare any many-to-many link types edited with `OntologyLinkType(SourceObjectType, "linkApiName")`, also imported from `functions.api`. For more information, see Declare edited link types.
  * Have an explicit `list[OntologyEdit]` return type hint imported from `functions.api`.



## Construct an Ontology edits container

To perform Ontology edits in a Python function, first construct an Ontology edits container from the [OSDK client](/docs/foundry/functions/python-functions-on-objects/). For example:
    
    
    Copied!
    
    1
    ontology_edits = FoundryClient().ontology.edits()

This container is used to keep track of all edits made in a function.

## Update properties

Ontology objects in Python functions are read-only by default. Attempts to modify their properties will raise an exception.

In order to edit an object, first obtain an editable view of that object using an Ontology edits container, either from an existing object instance:
    
    
    Copied!
    
    1
    editable_object = ontology_edits.objects.MyObjectType.edit(my_object)

or given an object primary key:
    
    
    Copied!
    
    1
    editable_object = ontology_edits.objects.MyObjectType.edit(object_primary_key)

Once you have an editable object, you can edit property values by reassigning the property value for an object. For example:
    
    
    Copied!
    
    1
    editable_employee.last_name = new_name

Subsequent access to the `last_name` property value of `editable_employee` later in the same function execution will yield the new value that was just set. However, the original non-editable object will _not_ reflect the changes.

Array properties on editable objects are mutable `list` values, but only assigning to the property records the edit, so modifying the list in place is silently dropped. To modify an array, create a copy of it, modify the copy, then update the property:
    
    
    Copied!
    
    1
    2
    3
    4
    5
    6
    # Copy to a new array
    array_copy = list(editable_object.my_array_property)
    # Now you can modify the copied array
    array_copy.append(new_item)
    # Then overwrite the property value
    editable_object.my_array_property = array_copy

The primary key property value of an existing object cannot be updated.

## Edit struct properties

Beta

Struct properties are a beta feature in Python functions. You can enable struct properties with the `beta` argument on the `@function` decorator, and import the generated struct property class from the object-specific Ontology SDK module.

When creating an object, first call `create()`, and then assign the struct property on the returned editable object. Struct properties cannot be passed directly to `create()` as keyword arguments.
    
    
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
    from functions.api import function, OntologyEdit
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import MyObjectType
    from ontology_sdk.ontology.objects.my_object_type import MyStructPropertyClass
    
    @function(beta=True, edits=[MyObjectType])
    def create_object(primary_key: str) -> list[OntologyEdit]:
        ontology_edits = FoundryClient().ontology.edits()
    
        new_object = ontology_edits.objects.MyObjectType.create(primary_key)
        new_object.my_struct_property = MyStructPropertyClass(
            first_field="Example value",
            second_field=True,
        )
    
        return ontology_edits.get_edits()

The generated struct property class translates its Python field names to the corresponding Ontology API names. A user-defined class or `dataclass` cannot be used in its place.

To update a field within an existing struct property, reassign the complete struct property to the editable object after changing the field:
    
    
    Copied!
    
    1
    2
    3
    4
    5
    editable_object = ontology_edits.objects.MyObjectType.edit(my_object)
    
    struct_property = editable_object.my_struct_property
    struct_property.first_field = "Updated value"
    editable_object.my_struct_property = struct_property

Changing a field directly, such as `editable_object.my_struct_property.first_field = "Updated value"`, does not register an Ontology edit.

## Update links

Single-link and multi-link properties have various methods for updating links:
    
    
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
    # Set an Employee's supervisor
    editable_employee.supervisor.set(new_supervisor)
    
    # Clear an Employee's supervisor
    editable_employee.supervisor.clear()
    
    # Add a new report to the given employee
    editable_employee.reports.add(new_report)
    
    # Remove an old report associated with the given employee
    editable_employee.reports.remove(new_report)

As with updating properties, accessing links of `editable_employee` after they have been updated will reflect the updates you have made.

### Declare edited link types

A function that edits a many-to-many link must declare the link type in the `edits` parameter of the `@function` decorator using `OntologyLinkType`, ensuring actions receive complete [provenance](/docs/foundry/action-types/function-actions-getting-started/#provenance) information. `OntologyLinkType` takes the object type you traverse from and the link type API name defined on that object type. This API name is the Ontology API name of the link type, which may differ from the attribute used to access the link on an editable object.

For a complete example of adding and removing links, see the Python tab in [Link type edits](/docs/foundry/functions/edits-overview/#link-type-edits).

One-to-one and one-to-many links are backed by a foreign key property on one of the two object types, so setting or clearing them produces an object edit rather than a link edit. For those link types, declare the object type that holds the foreign key instead of declaring a link type.

## Create objects

You can create new objects using the `MyObjectType.create()` method on the Ontology edits container. When creating a new object, you must specify a value for its primary key.

In this example, we create a new Ticket object with the given ID, set its `due_date` property, and assign it to the given Employee by modifying the `assigned_tickets` link.
    
    
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
    from datetime import datetime, timedelta
    
    from functions.api import function, Integer, Array, OntologyEdit, OntologyLinkType
    from ontology_sdk import FoundryClient
    from ontology_sdk.ontology.objects import Employee, Ticket
    
    @function(edits=[Employee, Ticket, OntologyLinkType(Employee, "assignedTickets")])
    def create_new_ticket_and_assign_to_employee(
        employee: Employee,
        ticket_id: Integer
    ) -> list[OntologyEdit]:
        ontology_edits = FoundryClient().ontology.edits()
    
        new_ticket = ontology_edits.objects.Ticket.create(ticket_id)
        new_ticket.due_date = datetime.now() + timedelta(days=7)
    
        editable_employee = ontology_edits.objects.Employee.edit(employee)
        editable_employee.assigned_tickets.add(new_ticket)
    
        return ontology_edits.get_edits()

Non-struct property values may also be passed directly to the create method in addition to the primary key. For example:
    
    
    Copied!
    
    1
    2
    new_due_date = datetime.now() + timedelta(days=7)
    new_ticket = ontology_edits.objects.Ticket.create(ticket_id, due_date=new_due_date)

## Delete objects

You can delete an object by calling the `MyObjectType.delete()` method on the Ontology edits container.

In this example, we delete all the tickets assigned to the given employee:
    
    
    Copied!
    
    1
    2
    for ticket in employee.tickets:
        ontology_edits.objects.Ticket.delete(ticket)

Objects may also be deleted using a primary key instead of an instance:
    
    
    Copied!
    
    1
    ontology_edits.objects.Ticket.delete(ticket_id)

[âPREVIOUSCreate a custom aggregation](/docs/foundry/functions/python-functions-create-custom-aggregation/)

[NEXTStaged writesâ](/docs/foundry/functions/python-staged-writes/)