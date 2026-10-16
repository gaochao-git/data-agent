# Palantir Foundry Docs — /docs/foundry/interfaces/interface-metadata

> 来源: https://www.palantir.com/docs/foundry/interfaces/interface-metadata/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Metadata reference

An interface is represented in the Ontology by the following metadata:

  * **RID:** An automatically generated unique identifier for every resource in Palantir. An interfaceâs RID will be referenced in error messages across the platform.
  * **Icon:** A picture and color used as a visual identifier that will appear in applications when a user views this interface. Interfaces have dashed lines around their icons to visually distinguish them from object type icons. For example, the building icon surrounded by dashed lines may be used to depict the `Facility` interface.
  * **Display name:** The name shown to anyone accessing this interface in user applications. For example, the display name for the `Facility` interface may be "Facility".
  * **Description:** Explanatory text about the interface that anyone can read in user applications. For example, the description of the `Facility` interface may be "An abstract object type interface for representing airline facilities".
  * **API name:** The name used when referring to the interface programmatically in code. For example, the API name of the `Facility` interface may be `facility`.
  * **Status:** A signal to users and Ontology builders about where in the development process the interface stands. It can be `active`, `experimental`, `example`, or `deprecated`. By default, the `Facility` interface will have the `experimental` status. Learn more about [statuses](/docs/foundry/object-link-types/metadata-statuses/).
  * **Searchable:** A Boolean value that specifies whether the interface is searchable. Searchable interfaces enable users to load or search all objects of the interface at once. Searchable interfaces are limited to 75 implementing object types, whereas non-searchable interfaces are limited to 1,000. By default, the `Facility` interface will be searchable.



[âPREVIOUSExtend an interface](/docs/foundry/interfaces/extend-interface/)

[NEXTOntology design: Best practicesâ](/docs/foundry/ontology/ontology-best-practices/)