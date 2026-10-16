# Palantir Foundry Docs — /docs/foundry/object-monitors/input

> 来源: https://www.palantir.com/docs/foundry/object-monitors/input/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Input

Sunset

Object Monitors are superseded by [Automate](/docs/foundry/automate/overview/). Automate is a fully backward-compatible product that offers a single entry point forÂ all business automationÂ in the platform.

Monitor inputs are defined using [object sets](/docs/foundry/analytics/datasets-object-sets/#object-sets). The monitor [condition](/docs/foundry/object-monitors/condition/) may then reference attributes of the inputs. An input may be used to calculate a metric such as an aggregate, or to monitor when objects are added or removed from that input.

Input object sets are created by building a **saved exploration** in [Object Explorer](/docs/foundry/object-explorer/save-explorations/). You can add saved explorations as monitor inputs [directly in Object Explorer](/docs/foundry/object-monitors/create_new_object_monitor/#create-from-object-explorer), or when configuring a new monitor [in the Object Monitors application](/docs/foundry/object-monitors/create_new_object_monitor/#create-from-object-monitors-application).

The monitor input is displayed in the overview section when viewing an object monitor in the Object Monitors application. Click on a monitor to open the overview panel.

When viewing a particular saved exploration in Object Explorer, object monitors that use this exploration as input are displayed in the **Monitor** popover in the upper right of your screen.

[âPREVIOUSMonitors](/docs/foundry/object-monitors/monitor/)

[NEXTConditionsâ](/docs/foundry/object-monitors/condition/)