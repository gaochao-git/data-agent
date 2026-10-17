# Palantir Foundry Docs — /docs/foundry/workshop/variable-recompute

> 来源: https://www.palantir.com/docs/foundry/workshop/variable-recompute/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Variable recompute behavior

Recomputing a variable calculates its value again from its definition and current inputs. For example, a total cost variable might use a quantity variable as an input. Recompute behavior controls whether changing the quantity updates the total automatically or waits for a user to request an update.

## Choose a recompute behavior

Open the **Variables** panel in the Workshop editor, select a variable, and choose an option under **Recompute variable value**. This setting is available for the [supported variable definitions](#supported-variable-definition-types) listed below.

| Option | Behavior | Example use |
| --- | --- | --- |
| **Automatically** | Computes when needed and recomputes when its inputs change. This is the default. Data refreshes can also cause recomputation, even when input values appear unchanged. | Keep a total or summary in sync with a user's selections. |
| **Only when triggered by an event** | Waits for an explicit **Recompute {variable name}** event to request computation. Input changes and data refresh of an upstream variable do not automatically recompute the value. | Run a calculation after a user finishes entering several inputs and selects a button. |
| **On module load, and when triggered by an event** | Computes an initial value when the module loads, subject to lazy loading. After that, recomputes when an explicit **Recompute {variable name}** event is triggered, rather than whenever inputs change or data refreshes for an upstream variable. | Start with a calculated default, then let a user decide when to update it. |

With either event-based option, changing inputs or refreshing data for an upstream variable does not automatically bring the calculated value up to date. Configure a recompute event when the value needs to reflect those changes. These settings control calculation from the variable's definition; widgets and **Set variable value** events can still change its value directly.

### Trigger a recompute

Add a [**Recompute {variable name}** event](/docs/foundry/workshop/concepts-events/#recompute-variable-name) to a widget, such as a [Button Group](/docs/foundry/workshop/widgets-button-group/). The event requests a new value using the variable's definition and current inputs, regardless of which recompute option is selected.

For an event-only calculation, provide a way to trigger the event before users need its result. Recomputing a variable does not force its inputs to recompute if their own settings require an event. Ensure those inputs are ready first.

## Supported variable definition types

Availability depends on the definition type used, and is supported for these definition types:

- **Function**
- **Object set aggregation**
- **Object property**
- **Variable transformation**
- **Object set filter**
- **SQL query**

## Loading and data refresh

Workshop uses [lazy variable loading](/docs/foundry/workshop/concepts-variables/#lazy-variable-loading) to avoid unnecessary calculations. Automatic computation can wait until a variable is needed by a visible widget or layout, including when another required variable depends on it. Consequently, **On module load, and when triggered by an event** can compute its initial value the first time it is needed, rather than immediately when you open the module.

Visibility is not limited to values displayed on screen. Variables that control layouts, such as conditional visibility, can be treated as visible even when the section they control is hidden. Hiding a section therefore does not guarantee that all related calculations stop.

Submitting an action or refreshing module data can cause automatic variables to recompute. This includes functions, aggregations, object properties, and transformations. These variables can also recompute after an [auto-refresh](/docs/foundry/workshop/auto-refresh/) update, even if the resulting value is unchanged.

An object set variable's value and the object data displayed by a widget are separate. Controlling recomputation of a function that returns an object set does not freeze the objects shown in a table. The widget can still reload its data after an action or data refresh.

## Known limitations

- **Recomputation can overwrite user changes.** If a widget or **Set variable value** event changes a computed variable, a later recomputation replaces that value with the result of its definition. Use an event-based option where supported, or keep user-entered values in a separate static variable, when those changes should persist during the session.
- **Events do not wait for calculations to finish.** Placing **Set variable value** after **Recompute {variable name}** in the same event list does not guarantee that it copies the new result. It can copy the previous value or a loading state. Separate dependent steps into user-triggered interactions and wait for the calculation to finish before continuing. See [event execution order](/docs/foundry/workshop/concepts-events/#event-execution-order).
- **Object set definition does not support recompute behavior.** The **Object set definition** variable definition type does not directly support recompute behavior. You can instead configure the behavior of supported variables used as its inputs, such as an object set filter, or use a function to define the object set.

Use the [variable lineage graph](/docs/foundry/workshop/concepts-variables/#variable-lineage-graph) to inspect dependencies and identify which widgets read or change a variable when troubleshooting unexpected recomputation.
