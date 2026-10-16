# Palantir Foundry Docs — /docs/foundry/object-monitors/create_new_object_monitor

> 来源: https://www.palantir.com/docs/foundry/object-monitors/create_new_object_monitor/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Create a new object monitor

Sunset

Object Monitors are superseded by [Automate](/docs/foundry/automate/overview/). Automate is a fully backward-compatible product that offers a single entry point forÂ all business automationÂ in the platform.

You need data integrated into your Foundry Ontology before you can create an object monitor. If your data is not yet integrated, learn how to [create your Ontology](/docs/foundry/ontology/overview/) first.

## Create from Object Explorer

Monitors can be created in Object Explorer after saving an exploration by clicking **Monitor** in the upper right of your screen. An exploration can be saved by clicking **Save** in the exploration view and selecting a Project or folder destination.

### Create an object monitor

After saving the exploration, click **Monitor** to open a view showing all object monitors using this exploration as an input. In newly created explorations, this list is empty.

Click **Add new Monitor** to open a simplified setup view for a new monitor.

Add a monitor name, optional description, and monitor [condition](/docs/foundry/object-monitors/condition/).

Advanced conditions containing nested sub-conditions cannot be configured from this view. Instead, they must be created and modified from the Object Monitors application.

You may also optionally change the default save location from **Private** to a Public project. If you plan to have additional subscribers, we recommend storing the saved exploration and monitor in a shared Project.

After entering the required information and clicking save, you will return to the list of monitors for the exploration. This list will now contain the newly created monitor.

### After saving

Additional options are available once a monitor has been saved.

Metadata about the monitor, including its save location, creation time, and last updated time, are shown in the **Details** tab. This tab also displays the [expiration date](/docs/foundry/object-monitors/monitor/#expiration) and allows you to extend the expiration three months into the future.

Subscribers may be added or removed from the **Subscribers** tab, and notifications can be enabled or disabled per subscriber.

Actions may be optionally configured from the **Actions** tab.

Learn more about [configuring Actions](/docs/foundry/object-monitors/actions/) with Object Monitors.

The quick actions dropdown provides options for disabling or muting the monitor and an option to delete the monitor by moving it to the trash.

## Create from Object Monitors application

The Object Monitors application shows an overview of all available monitors across all Projects for a given user. Follow these steps to create a new monitor in the application interface.

  1. Create a new monitor by clicking **Add Monitor** in the upper right corner.



  2. Select a save location for the new monitor. For monitors that will have multiple subscribers, we recommend storing them in a shared Project.



  3. Provide the full condition configuration. Configuration options vary depending on if you choose to use [event](/docs/foundry/object-monitors/condition/#event) or [threshold](/docs/foundry/object-monitors/condition/#threshold) conditions. The monitor [inputs](/docs/foundry/object-monitors/input/) must use existing saved explorations created in Object Explorer. If the required input exploration does not exist, [create it](/docs/foundry/object-explorer/save-explorations/) in Object Explorer and then return to this step.



Additional subscribers may be added from the **Subscribers** tab.

  4. Click **Save** to store and enable your new monitor.



[âPREVIOUSOverview](/docs/foundry/object-monitors/overview/)

[NEXTCore concepts / Monitorsâ](/docs/foundry/object-monitors/monitor/)