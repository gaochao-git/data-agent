# Palantir Foundry Docs — /docs/foundry/object-link-types/object-types-overview

> 来源: https://www.palantir.com/docs/foundry/object-link-types/object-types-overview/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Object types

An **object type** is the schema definition of a real-world entity or event. An **object or object instance** refers to a single instance of an object type; an object corresponds to a single real-world entity or event. An **object set** refers to a collection of multiple object instances; that is, an object set represents a group of real-world entities or events.

For example, in the Ontology Manager, you may create an `Employee` object type that defines the characteristics for âAll employeesâ or all objects of that type. An object refers to a single instance of the `Employee` object type, like the notional employees âMelissa Changâ, âAkriti Patelâ, or âDiego Rodriguez.â A group of objects like âAll tenured employeesâ represents an object set.

Similarly, in the Ontology Manager, you may create a `Flight` object type that defines characteristics for âAll flightsâ or all objects of that type. An object refers to a single instance of the `Flight` object type, like âJFK â SFO 2021-02-24â or âTLV â LHR 2020-04-16.â A group of objects like âAll arrived flightsâ represents an object set.

The concepts underpinning the Ontology have analogous concepts in the structure of a dataset. The definition of an object type in the Ontology is analogous to that of a dataset, while the definition of an object is analogous to that of a row in the dataset. The definition of an object set is analogous to a filtered set of rows in a dataset. For example, an `Employee` dataset may define the schema for âAll employee rows.â In this case, a single row refers to a single employee, like âMelissa Chang,â âAkriti Patel,â or âDiego Rodriguez.â If you filter the dataset based on tenure, you will have a set of rows that represent âAll tenured employees.â

Rather than being an abstract data model, the Foundry Ontology maps each ontological concept to an organization's actual data, enabling this data asset to power real-world applications. Objects are created and displayed in user applications by adding backing datasources or configuring actions that create objects of an object type in Ontology Manager. To create objects of type `Employee` from existing data, an organization can add backing datasources to the `Employee` object type and connect its employee directory and other enterprise data into the Ontology. If every object will be created through actions, you can instead [create the object type without a backing datasource](/docs/foundry/object-link-types/create-object-type/#create-an-object-type-without-a-backing-datasource).

Get started by learning how to [create an object type](/docs/foundry/object-link-types/create-object-type/).

[âPREVIOUSTypes reference](/docs/foundry/object-link-types/type-reference/)

[NEXTCreate an object typeâ](/docs/foundry/object-link-types/create-object-type/)