# Palantir Foundry Docs — /docs/foundry/action-types/consistency-guarantees

> 来源: https://www.palantir.com/docs/foundry/action-types/consistency-guarantees/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

, the user submitting the action needs the appropriate permissions for the action log object type. The same requirement applies to any other object type the action creates or modifies. Without those permissions, the action fails at submission.# Consistency and isolation

An action applies edits to the Ontology through a single transaction that can read from the Ontology, apply user-defined logic, and write edits to one or more objects and links.

This page describes the guarantees that action types provide during concurrent execution and how to choose the right isolation level for a particular use case.

## ACID guarantees

Actions behave as database transactions and provide the standard ACID properties:

  * **Atomicity:** An action's edits to the Ontology are applied as a single, all-or-nothing batch. This does not extend to other [side effects](/docs/foundry/action-types/side-effects-overview/) of the action, such as notifications or webhooks, or to side effects of a backing function, such as external calls it makes.
  * **Consistency:** An action's edits are committed only if they satisfy the Ontology's constraints, such as [value type constraints](/docs/foundry/object-link-types/value-type-constraints/) and [required properties](/docs/foundry/object-link-types/required-properties/).
  * **Isolation:** Governs how simultaneous actions affect each other. The section below explains the isolation levels offered for action types.
  * **Durability:** Once an action commits, its edits are permanently persisted.



After an action completes, any action or query that starts afterward sees all edits applied by the completed action. This guarantee does not apply to reads performed during the same action execution.

The visibility of those edits depends on the action's write mode, as described in Write modes.

## How writes work

### What counts as a write to a property

Whether an action writes to a property depends on how the action is implemented:

  * In a logic rule action, a property is written when the rule [maps a parameter to that property](/docs/foundry/action-types/rules/#values-and-parameters).
  * In a [function-backed action](/docs/foundry/action-types/function-actions-overview/), a property is written when the function sets it through an [Ontology edit](/docs/foundry/functions/edits-overview/).



OSDK action requests

When you apply an action through the [Ontology SDK (OSDK)](/docs/foundry/ontology-sdk/typescript-osdk/#apply-action), you can omit an optional parameter from the action request only if the parameter has a default value configured. If the default value uses the [current value of an object property](/docs/foundry/action-types/parameters-default-value/#object-property-default-values), the action fills in the omitted parameter with that property value and writes it back to the property.

### Write modes

Actions provide two write modes. In both modes, the action's edits are collected while the action runs and committed to the Ontology as a single atomic batch at the end, so other actions never observe a partially applied action. The two modes differ in whether the action's **own** reads can see the edits it has made so far:

  * **Batched writes (default):** Edits are collected into a batch as the action runs and committed to the Ontology as a single atomic operation at the end. The action's own reads cannot see its batched edits; every read returns data as it is in the Ontology, without the edits this action has collected so far. For example, if an action updates an object's property and then reads that object again, the read returns the original value, not the updated one.
  * **Staged writes:** The action's reads can see its own edits. In the same action execution, reading an object the action has edited returns the edited value, and searches and aggregations include the edits as well. Staged writes are currently supported for [TypeScript v2](/docs/foundry/functions/typescript-v2-staged-writes/), [Python](/docs/foundry/functions/python-staged-writes/), and [AIP Logic](/docs/foundry/logic/staged-writes/) function-backed action types.



### How conflict checks work

At isolation levels that perform conflict checks, the Ontology detects write-write conflicts at the object level. If two actions concurrently write to the same object, one action fails with a conflict error. This applies even if the actions write to different properties of that object.

A rebuild of a dataset that backs an object type or many-to-many link type can also cause conflicts. Conflicts between action executions are limited to objects that both actions write, but conflicts caused by backing dataset rebuilds are broader. An action may encounter a conflict while a rebuild is running even if the rebuild does not change the specific object that the action writes. These conflicts are transient, so retrying the action after the rebuild completes typically succeeds.

When a conflict is detected, the failing action's entire edit is discarded. See Retry mode on transient errors for how conflicts are surfaced and retried.

## Isolation levels

An action type's _isolation level_ sets the guarantees that apply when an action runs alongside other actions interacting with the same data. It defines two things: which version of the data each read inside the action observes, and which concurrent changes cause the action to fail rather than commit a result built on data that has since changed. A stronger level rules out more anomalies but rejects more actions on conflict; a weaker level commits more often but offers fewer guarantees.

An action type's isolation level is configured in **Ontology Manager** , in the action type's **Capabilities** tab. The level can be changed after creation and takes effect for subsequent executions.

All new action types that use batched writes are created with **snapshot isolation** by default. Action types that use staged writes currently support only legacy mode. For more information, see Current staged writes behavior.

The levels available for action types are summarized below and described in detail in the sections that follow.

Level| Reads| Conflict checks  
---|---|---  
**Legacy**|  Reads are not guaranteed to come from a single point in time. Different reads in the same action can observe different states.| Fails if another action changed an object this action **wrote**.  
**Snapshot isolation**|  All reads use one snapshot, taken when the action starts.| Fails if another action changed an object this action **wrote**.  
**Read committed**|  Each read returns the latest committed value at the time of the read.| Allows edits despite concurrent changes. For properties set by both actions, the last action to commit wins.  
  
### Legacy

The legacy isolation mode remains available in the **Capabilities** tab for action types created before explicit isolation levels were introduced. New action types that use batched writes default to snapshot isolation instead.

**Reads:** Reads usually come from a single point in time, but this is not guaranteed. Different objects loaded during the same action execution may reflect different states of the Ontology because the underlying data can be refreshed while the action is running due to errors in retrieving the data from object storage.

**Writes:** Legacy isolation supports both write modes. With batched writes, reads performed later during the same action execution do not include edits made earlier in that execution. With staged writes, later reads include those edits. For more information, see How writes work and Current staged writes behavior.

**Conflict checks:** An action fails with a conflict error if another action changes an object it writes between the time the object is loaded and the time the action commits. However, because reads are not guaranteed to be consistent, a decision based on data the action only _read_ may be made on a view that combines data from different points in time.

The legacy isolation mode is provided for backward compatibility.

For new action types, use snapshot isolation to get the same conflict checks with the additional guarantee that all reads come from a single point in time.

### Snapshot isolation

This is the default level for new action types that use batched writes. For the behavior of action types that use staged writes, see Current staged writes behavior.

Sources may observe different data

Logic rule and function-backed actions read the Ontology from one consistent point in time. When a function calls a [source](/docs/foundry/functions/source-aliases/), the source operates outside the action's point-in-time view. A source may read from the Ontology or other systems, and those reads may observe data from a different point in time. The action's atomicity and conflict checks do not cover operations that a source performs.

**Reads:** Every Ontology read performed directly by the action observes a single consistent point-in-time view, taken when the action begins. Reading the same object or running the same search twice always returns the same matching data, regardless of what other actions commit while this action runs.

**Writes:** The action uses batched writes, so reads performed later during the same action execution do not include edits made earlier in that execution.

**Conflict checks:** An action fails with a conflict error if another action changes an object it writes between the time the object is loaded and the time the action commits. The action fails rather than overwriting the concurrent change. Objects the action only _reads_ do not contribute to conflict failures at this level. For information about retrying conflicts, see Retry mode on transient errors.

#### Write skew

Because only _written_ objects are conflict-checked, snapshot isolation does **not** detect read-write conflicts.

Two actions can each read overlapping data, make a decision based on that read, write to _different_ objects, and both commit â even though neither would have committed had it seen the other's write.

This is known as _write skew_.

**Example:** An on-call rotation must always keep **at least one** engineer on call. An engineer may leave the rotation only if someone else remains, so the action checks: _are at least two engineers currently on call?_ Engineers A and B are both on call, and each runs the action to remove themselves at the same time:

  * A's action reads the rotation from its snapshot, sees two engineers on call, and proceeds â removing A still leaves B.
  * B's action reads the same state from its own snapshot, sees two engineers on call, and proceeds for the same reason.
  * A writes `A.onCall = false`; B writes `B.onCall = false`. The two actions wrote _different_ objects, so there is no write-write conflict and both commit. The rotation is now empty, violating the invariant.



Snapshot isolation cannot catch this because the violated invariant lives on data the actions _read_ (the set of on-call engineers), not on any single object either action _wrote_.

To avoid this anomaly, redesign the workflow to express the invariant as a write to a single shared object.

For example, both actions could write to a `Rotation` object that has an `onCallCount` property, causing the actions to conflict.

### Read committed

The **read committed** isolation mode will be available soon.

**Reads:** Each read returns the latest committed value at the moment the read runs. There is no fixed snapshot, so two reads taken at different points in the same action can observe different states if another action commits between reads.

**Writes:** The action uses batched writes, so reads performed later during the same action execution do not include edits made earlier in that execution (see How writes work).

**Conflict checks:** Actions allow edits even when another action changed the same object during execution. When both actions commit, changes to different properties are preserved. If both actions set the same property, the last action to commit determines its value. A small number of conflicts can still occur occasionally when running a large number of actions concurrently on the same objects.

**Example:** A `Ticket` object has `status = "open"` and `priority = "low"`. Two actions run at the same time:

  * Action A sets `status = "in progress"` and `priority = "medium"`.
  * Action B sets `priority = "high"`.
  * Action A commits first. The ticket is now `status = "in progress"`, `priority = "medium"`.
  * Action B commits second and overwrites A's change to `priority`. The ticket ends up with `status = "in progress"` and `priority = "high"`. Action A's change to `status` and Action B's change to `priority` are both preserved.



Under snapshot isolation, Action B would instead fail its conflict check because both actions wrote the same object, and Action A's edit would be preserved.

This level produces higher commit success rates at the cost of weaker correctness guarantees. It is appropriate when an action only writes values it already has in hand â such as values passed in as parameters â and does not rely on what it reads to decide what to write.

Concurrent edits may be overwritten

Use read committed with caution when multiple actions can set the same property concurrently. If both actions commit, the later action overwrites the earlier value of that property. Changes to other properties are preserved.

## Current staged writes behavior

Action types that use staged writes currently support only legacy isolation mode.

This section describes the guarantees that staged writes provide.

**Reads:** Each read returns the latest committed value at the moment the read runs, plus the action's own staged edits. There is no fixed snapshot, so two reads taken at different points in the same action can observe different states if another action commits between reads. However, the action's own staged edits are always visible to its later reads.

**Writes:** The action uses staged writes, so reads performed later during the same action execution include edits made earlier in that execution. Other actions do not see the edits until they are committed atomically at the end (see How writes work).

**Conflict checks:** An action fails with a conflict error if another action changes an object it writes between the time the object is loaded and the time the action commits. The action fails rather than overwriting the concurrent change. Objects the action only _reads_ do not contribute to conflict failures. For information about retrying conflicts, see Retry mode on transient errors.

This behavior combines the read-your-own-writes capability of staged writes with write-write conflict detection, but does not guarantee that reads from the Ontology come from a single point in time.

## Retry mode on transient errors

The platform can automatically retry an action when it encounters a transient error, including conflict errors and failures loading objects from object storage. Each retry re-executes the action from scratch using the action's configured isolation level, up to 5 attempts. Logic that reads current values recomputes its edits using the data available to the new execution. If every attempt fails, the error is returned to the caller.

By default, actions without external system calls (such as webhooks) are retried, while actions with external system calls are not â because re-executing the action would repeat its side effects, for example firing a webhook twice.

You can adjust this behavior per action type in the **Capabilities** tab using the **Retry mode on transient errors** setting:

  * **Only actions without external calls (default):** The platform retries only when the action type has no external system calls.
  * **All actions:** The platform retries all actions, including those with external calls. Use this when the action type's external calls are known to be safe to repeat (idempotent).
  * **Disabled:** The platform never retries automatically. The error is returned to the caller on the first failure. Use this when the caller wants to observe and handle every conflict.



Note that two otherwise identical action types can behave differently under contention depending on this setting â one appears to always succeed because conflicts are retried away, while the other surfaces conflict errors to its callers.

Automatic retries also mean that an action that conflicts often can take noticeably longer to complete, even when it ultimately succeeds.

## Choosing an isolation level

  * **Use snapshot isolation** unless you have a specific reason not to. Snapshot isolation is the best isolation level for the majority of workflows, including read-modify-write logic on individual objects.
  * For action types created _before_ explicit isolation levels were introduced, set the level to snapshot isolation so that every read in an execution is guaranteed to come from the same point in time, rather than the legacy behavior where this usually holds but is not guaranteed. However, because snapshot isolation uses the same point-in-time view throughout the execution, an action may be retried more often if it runs for a long time and attempts to edit data that is updated frequently.
  * Consider read committed **only** when conflict failures are frequent, you understand the anomalies it permits, and your action's writes do not depend on a stable read of the data. Review warnings in the read committed section before switching an existing action type to this mode.



[âPREVIOUSRead and write authorizations](/docs/foundry/action-types/read-write-authorizations/)

[NEXTMonitoringâ](/docs/foundry/action-types/monitoring/)