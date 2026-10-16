# Palantir Foundry Docs — /docs/foundry/object-edits/user-edit-history

> 来源: https://www.palantir.com/docs/foundry/object-edits/user-edit-history/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Enable user edit history

Tracking the history of user edits to objects indexed into Object Storage v2. Enable or disable this feature using the **Track user edit history** toggle in the **Datasources** tab of the Ontology Manager, as shown in the screenshot below.

To allow user edits, the [Edits toggle](/docs/foundry/object-edits/how-edits-applied/) must be enabled.

Edit history records every edit to an object, regardless of how the edit was made. To capture the decision behind an edit instead, including the parameter values a user submitted and the context at the time of submission, enable [action log](/docs/foundry/action-types/action-log/) on the action type.

## Edit history timestamps

Edit history uses a UTC commit timestamp that the action captures as it prepares to submit edits to Object Storage v2. When the action type has an [action log](/docs/foundry/action-types/action-log/#action-log-timestamps), the action log and edit history entries for the same action use the same timestamp. This timestamp does not indicate the exact time that edits become visible in queries.

The timestamp supplied by **Current User/Time** in [action rules](/docs/foundry/action-types/rules/#values-and-parameters) represents the start of action execution and stays fixed throughout execution. A timestamp property set by this rule can therefore have an earlier value than the edit history timestamp.

## Common issues and notes

  * The edit history reflects the changes made to objects after enabling the **Track user edit history**. Any changes prior to the activation of this feature will not be tracked.
  * Once you enable the **Track user edit history** , it takes a few minutes to initialize. During this short period, users cannot perform actions on these objects.
  * If you are migrating from Object Storage v1 to Object Storage v2, ensure that `preserve edit history` is checked during the migration if you want to retain the edit history.
  * Users who have access to the current state of an object (object with the same primary key) can access the entire history of the object. This implies that if an object is deleted and recreated, users can still see the history that occurred prior to the deletion action.



After enabling **Track user edit history** , the [Edit History widget](/docs/foundry/workshop/widgets-edits-history/) can be added to Workshop modules or Workshop-backed object views to display edit histories.

## Disable edit history

Disabling **Track user edit history** permanently deletes all existing edit histories for this object type. Before saving the ontology, users will receive a warning and must confirm an acknowledgment that edit history will be deleted.

[âPREVIOUSHow user edits are applied](/docs/foundry/object-edits/how-edits-applied/)

[NEXTPermission checks for Actionsâ](/docs/foundry/object-edits/permission-checks/)