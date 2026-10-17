# Palantir Foundry Docs — /docs/foundry/quiver/card-event-indicator-series

> 来源: https://www.palantir.com/docs/foundry/quiver/card-event-indicator-series/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Event indicator series

An event indicator series creates a time series out of an event set indicating the number of events occurring at a given time.

- Only events with non-zero durations will be plotted.
- To manually shift the start or end time of the events, use the [time shift event set](/docs/foundry/quiver/card-time-shift-event-set/) card to modify the input event set.

## Input type

Event set

## Output type

Time series

## Examples

![Event indicator series example.](/docs/resources/foundry/quiver/resource-events-series-indicator-example.png)

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |
