# Palantir Foundry Docs — /docs/foundry/quiver/card-bar-chart

> 来源: https://www.palantir.com/docs/foundry/quiver/card-bar-chart/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Bar chart

Create a horizontal or vertically oriented bar chart of your objects. Bar chart categories are determined by your object properties, and you can set values to count objects or show an aggregation of a property value or values. You can also use a bar chart to convert data into a time series.

- Supported aggregation metrics: min, max, sum, average, count, unique count, percentile, standard deviation, and variance
- Percentile, standard deviation, and variance metrics are not supported for object types backed by [Object Storage v1 (Phonograph)](/docs/foundry/object-backend/overview/#object-databases).

## Input type

Object set

## Output type

Categorical chart, object selection

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |

## See also

[Line chart](/docs/foundry/quiver/card-line-chart/)
[Pie chart](/docs/foundry/quiver/card-pie-chart/)
[Categorical scatter plot](/docs/foundry/quiver/card-categorical-scatter-plot/)
