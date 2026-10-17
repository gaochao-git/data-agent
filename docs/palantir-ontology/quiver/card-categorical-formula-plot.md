# Palantir Foundry Docs — /docs/foundry/quiver/card-categorical-formula-plot

> 来源: https://www.palantir.com/docs/foundry/quiver/card-categorical-formula-plot/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Categorical formula plot

Create a new categorical (bar, line, scatter) plot by computing a formula on overlapping categories of existing categorical plots. Numerics, from aggregations or parameters, can also be in the formula.

## Input type

Categorical chart, number

## Output type

Categorical chart, object selection

## Examples

In the example below, we add two bar plots together and multiply by the value of a numeric parameter. When writing formulas here, computation between bar plots will be run on matching segments and group-by categories. Single numerical values will be applied to all bars.

![Categorical formula plot example](/docs/resources/foundry/quiver/resource-categorical-formula-plot-example.png)

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |
