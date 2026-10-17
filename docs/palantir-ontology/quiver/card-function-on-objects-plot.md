# Palantir Foundry Docs — /docs/foundry/quiver/card-function-on-objects-plot

> 来源: https://www.palantir.com/docs/foundry/quiver/card-function-on-objects-plot/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Code function categorical plot

Use a [Function](/docs/foundry/functions/overview/) to create a categorical chart (bar, line, scatter) in Quiver.

Configuring a code function categorical plot requires writing a function that returns either a `TwoDimensionalAggregation` or `ThreeDimensionalAggregation`.

The function should be the same as the one used for a [function-backed workshop chart layer](/docs/foundry/workshop/widgets-chart/#function-aggregations-function-backed-layers).

## Input type

Object set, single object

## Output type

Categorical chart

## See also

[Categorical formula plot](/docs/foundry/quiver/card-categorical-formula-plot/)

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |
