# Palantir Foundry Docs — /docs/foundry/quiver/card-tabular-time-series

> 来源: https://www.palantir.com/docs/foundry/quiver/card-tabular-time-series/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Tabular time series

Select a timestamp and a value column from an input transform table and return a time series.

## Input type

Object set, transform table

## Output type

Time series

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Supported\* |

\* In the transform table, this transform is called "group to time series", and must be performed on a date group and numeric group column (result of a group by transform).
