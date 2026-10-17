# Palantir Foundry Docs — /docs/foundry/quiver/card-rolling-aggregate

> 来源: https://www.palantir.com/docs/foundry/quiver/card-rolling-aggregate/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Rolling aggregate

Rolling aggregates are typically used to “smooth” a series and show an averaged version of it. For each point in your series, a Rolling aggregate will calculate a new point based on your window function and aggregate method.

- As an example, if you choose a window size of one week and method `average`, each point will be calculated by finding the average value over the previous week.
- If you choose a window size of three days and method `sum`, then each point will be the sum of the previous three days.
- Windows are calculated by previous values up to and including that point.

## Input type

Time series

## Output type

Time series

### Example

![Rolling aggregate example](/docs/resources/foundry/quiver/card-rolling-aggregate.png)

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Supported |
