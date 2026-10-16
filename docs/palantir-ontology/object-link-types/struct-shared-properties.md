# Palantir Foundry Docs — /docs/foundry/object-link-types/struct-shared-properties

> 来源: https://www.palantir.com/docs/foundry/object-link-types/struct-shared-properties/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Struct properties and shared property types

Struct properties can be used by local and shared property types. When converting, or promoting a local property type to a shared property type, struct fields need to be re-mapped. Local struct property types backed by shared property types will inherit shared property type fields except for the struct field resource identifiers (RIDs). Struct field metadata (display name, description, aliases) will then be inherited from the shared property type, but struct fields will keep their original RIDs.

## Create a struct type shared property

  1. In Ontology Manager, you can create a struct type shared property in two ways:

     * From the top right of the homepage, select **New > Shared property**

     * Select **Shared properties > \+ New shared property**

  2. In the main panel, select the **New shared property** button. This will open a helper where you can configure metadata of the shared property including the name, description, aliases, and API name. Then select **Next** to proceed.

  3. Configure the base type, value type, visibility, and whether to require values for the property.

  4. Select a project to save this shared property to.

  5. Back in Ontology Manager, select **Save** in the upper right corner to [make the change to your ontology](/docs/foundry/ontology-manager/save-changes/).




## Attach a struct type shared property

  1. In Ontology Manager, open the **Properties** tab and select the desired property from the **Properties** table.
  2. In the **Property editor** to the right, scroll down to **Shared property** and select a shared property under **Assign**. This will share property metadata among both properties.



**Note:** To add new struct fields after assigning a shared property type to a local struct property type, you must add the new struct fields to the shared property type and map them to datasource columns for all local struct property types that are backed by the shared property.

## Convert a struct property type into a shared property

The following instructions detail how to convert a struct property into a struct property backed by a shared property type.

  1. In Ontology Manager, open the **Properties** tab and select the desired property from the **Properties** table.
  2. In the **Property editor** to the right, scroll down and select **Convert to a shared property** , which backs the struct property by a shared property type.



[âPREVIOUSAutomapping struct types](/docs/foundry/object-link-types/struct-automapping/)

[NEXTDesignate struct main fieldsâ](/docs/foundry/object-link-types/struct-main-fields/)