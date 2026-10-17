# Palantir Foundry Docs — /docs/foundry/quiver/card-transform-table

> 来源: https://www.palantir.com/docs/foundry/quiver/card-transform-table/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Transform table

A [transform table](/docs/foundry/quiver/cards-transform-table/) is a local table used for flexible, low-scale analysis. It can be used for a number of different workflows in Quiver including:

- Converting between different data types.
- Joining and aggregating objects
- Editing tabular data in-line
- Deriving properties, and then performing operations on those derived properties such as filtering and plotting
- Performing batch time series operations

For a complete list of transforms available in transform tables, consult the [index of transform table transformations](/docs/foundry/quiver/cards-transform-table-index/).

Transform tables handle certain computations and visualizations differently than object sets. For details on property type stringification, categorical chart behavior, and pivot table dimension handling, see [transform table computation differences](/docs/foundry/quiver/transform-table-computation-differences/).

## Input type

Object set, transform table, event set, dataset, Dataset SQL, categorical chart, pivot table, time series chart, Ontology SQL, number array, string array, Boolean array, time array

## Output type

Transform table

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |
