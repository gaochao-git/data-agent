# Palantir Foundry Docs — /docs/foundry/quiver/card-periodic-aggregate

> 来源: https://www.palantir.com/docs/foundry/quiver/card-periodic-aggregate/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Periodic aggregate

Periodic aggregates are similar to Rolling aggregates except that they downsample the data.

- If you have daily data and perform a Rolling aggregate using a window of one week with an average function, your chart will return a series with one point per day, with each point representing the previous week’s average.
- However, if you do a Periodic aggregate with a window of one week, your new series will have one point per week rather than one point per day.

## Input type

Time series

## Output type

Time series

## Examples

![Periodic aggregate example](/docs/resources/foundry/quiver/card-periodic-aggregate.png)

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Supported |

## See also

[Rolling aggregate](/docs/foundry/quiver/card-rolling-aggregate/)
