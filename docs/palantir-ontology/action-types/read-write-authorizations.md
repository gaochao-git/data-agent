# Palantir Foundry Docs — /docs/foundry/action-types/read-write-authorizations

> 来源: https://www.palantir.com/docs/foundry/action-types/read-write-authorizations/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Read and write authorizations

Beta

Read and write authorizations are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment. Functionality and platform support may change during active development.

Read and write authorizations add security boundaries to an action type. Read authorization limits the marked data that an action can read directly or receive as input when invoked by another piece of logic. Write authorization sets minimum security requirements for data created or modified by an action. These authorizations supplement the user's existing permissions and the action type's [submission criteria](/docs/foundry/action-types/submission-criteria/). They do not grant access or replace existing permissions or submission criteria.

## Read authorization

Read authorization provides an additional upper bound on the data an action can read during execution or receive as input. Read authorization applies when Foundry loads values into parameters, including when another piece of logic invokes the action, and when function-backed actions perform reads. If data exceeds the configured read authorization, the action cannot load that data even if the user could otherwise access it.

The user applying the action must still have permission to access the data. If no markings are configured for read authorization, the action has no additional read boundary beyond the user's existing access.

## Write authorization

Write authorization defines the minimum security that data created or modified by an action must meet. Foundry validates the resulting data security and blocks the action if the result is below the configured minimum.

Write authorization does not grant a user access to markings, automatically add any configured markings, or make an otherwise invalid edit permissible. The user must have the required permissions, provide valid marking values where applicable, and pass the action type's submission criteria. If no markings are configured for write authorization, this feature does not enforce additional minimum output security.

A configured write authorization is not enforced for writes managed by the platform, including [action logs](/docs/foundry/action-types/action-log/) and [edit history](/docs/foundry/object-edits/user-edit-history/) objects. This means that security applied to action logs and edit history may be below the write authorization.

## Mandatory marking guarantees

[Mandatory markings](/docs/foundry/security/markings/#data-dependency) are intended to propagate along data dependencies so derived data retains the protections of its inputs. For actions, the action's logic sets the security applied to output data. An action can therefore produce output at the same, higher, or lower security than the data it receives or reads. Read and write authorizations set upper and lower bounds on what an action can do. Together, they guarantee the permitted security differences between the action's inputs and outputs.

Configuring write authorization to be less restrictive than read authorization allows the action to write data below the security boundary of data that the action can read. This intentionally severs mandatory marking propagation and may result in data declassification. The action type editor displays a warning whenever the read and write settings differ.

When read and write authorizations differ, Foundry checks the permissions of the user saving or publishing the action type. The user must have permission to declassify every mandatory marking severed between the read and write boundaries. If the check succeeds, the action type may perform the approved declassification; Foundry does not repeat this permission check at runtime.

Read and write authorizations are currently optional. If read authorization is not configured, Foundry does not perform declassification permission checks when the action type is saved or published. The action can therefore read data above its write boundary and write less restrictive data, which can cause a data spill. Configure read authorization whenever an action may read marked data.

## Authorization rules by marking category

Foundry combines configured markings according to their category:

Marking category| Read authorization| Write authorization  
---|---|---  
Classification markings| Follows the environment's configured hierarchy and conjunctive or disjunctive category rules.| Follows the environment's configured hierarchy and conjunctive or disjunctive category rules.  
Other mandatory markings| All configured markings are required.| All configured markings are required.  
Organizations| All configured Organizations are required.| At least one configured Organization is required.  
  
Classification behavior can vary between environments. Review [Classification-based Access Controls# Read and write authorizations

Beta

Read and write authorizations are in the [beta](/docs/foundry/platform-overview/development-life-cycle/) phase of development and may not be available on your enrollment. Functionality and platform support may change during active development.

Read and write authorizations add security boundaries to an action type. Read authorization limits the marked data that an action can read directly or receive as input when invoked by another piece of logic. Write authorization sets minimum security requirements for data created or modified by an action. These authorizations supplement the user's existing permissions and the action type's [submission criteria](/docs/foundry/action-types/submission-criteria/). They do not grant access or replace existing permissions or submission criteria.

## Read authorization

Read authorization provides an additional upper bound on the data an action can read during execution or receive as input. Read authorization applies when Foundry loads values into parameters, including when another piece of logic invokes the action, and when function-backed actions perform reads. If data exceeds the configured read authorization, the action cannot load that data even if the user could otherwise access it.

The user applying the action must still have permission to access the data. If no markings are configured for read authorization, the action has no additional read boundary beyond the user's existing access.

## Write authorization

Write authorization defines the minimum security that data created or modified by an action must meet. Foundry validates the resulting data security and blocks the action if the result is below the configured minimum.

Write authorization does not grant a user access to markings, automatically add any configured markings, or make an otherwise invalid edit permissible. The user must have the required permissions, provide valid marking values where applicable, and pass the action type's submission criteria. If no markings are configured for write authorization, this feature does not enforce additional minimum output security.

A configured write authorization is not enforced for writes managed by the platform, including [action logs](/docs/foundry/action-types/action-log/) and [edit history](/docs/foundry/object-edits/user-edit-history/) objects. This means that security applied to action logs and edit history may be below the write authorization.

## Mandatory marking guarantees

[Mandatory markings](/docs/foundry/security/markings/#data-dependency) are intended to propagate along data dependencies so derived data retains the protections of its inputs. For actions, the action's logic sets the security applied to output data. An action can therefore produce output at the same, higher, or lower security than the data it receives or reads. Read and write authorizations set upper and lower bounds on what an action can do. Together, they guarantee the permitted security differences between the action's inputs and outputs.

Configuring write authorization to be less restrictive than read authorization allows the action to write data below the security boundary of data that the action can read. This intentionally severs mandatory marking propagation and may result in data declassification. The action type editor displays a warning whenever the read and write settings differ.

When read and write authorizations differ, Foundry checks the permissions of the user saving or publishing the action type. The user must have permission to declassify every mandatory marking severed between the read and write boundaries. If the check succeeds, the action type may perform the approved declassification; Foundry does not repeat this permission check at runtime.

Read and write authorizations are currently optional. If read authorization is not configured, Foundry does not perform declassification permission checks when the action type is saved or published. The action can therefore read data above its write boundary and write less restrictive data, which can cause a data spill. Configure read authorization whenever an action may read marked data.

## Authorization rules by marking category

Foundry combines configured markings according to their category:

Marking category| Read authorization| Write authorization  
---|---|---  
Classification markings| Follows the environment's configured hierarchy and conjunctive or disjunctive category rules.| Follows the environment's configured hierarchy and conjunctive or disjunctive category rules.  
Other mandatory markings| All configured markings are required.| All configured markings are required.  
Organizations| All configured Organizations are required.| At least one configured Organization is required.  
  
Classification behavior can vary between environments. Review [Classification-based Access Controls](/docs/foundry/security/classification-based-access-controls/#conjunctive-and-disjunctive-classification-marking-categories) for details about classification hierarchies and conjunctive or disjunctive categories.

## Configure read and write authorizations

In the action type editor, enable **Access constraints** to configure authorizations. Enabling access constraints initializes both authorizations with no markings. Disabling access constraints clears both authorizations.

Use the **Write security** and **Read security** tabs to configure each authorization separately. The editor displays a warning when the settings differ. If an authorization contains values that you cannot view, those values appear as redacted and cannot be edited.

Before submitting an action, the action form can display **Minimum required security** based on write authorization. The status can be:

  * **Security passed:** The destination security is expected to meet the configured minimum.
  * **Security not passed:** The destination security is below the configured minimum.
  * **Canât be determined:** Foundry cannot determine the destination security before submission.
  * **Not set:** No write authorization markings are configured.
  * **Redacted:** The user cannot view the configured authorization.



The form status provides early feedback. Foundry performs the authoritative validation when the action is submitted and fails the action if the resulting data does not meet write authorization.

[âPREVIOUSPermissions](/docs/foundry/action-types/permissions/)

[NEXTConsistency and isolationâ](/docs/foundry/action-types/consistency-guarantees/)

# Permissions

Permissions apply to action types in the following ways:

  * Who can view a given action type?
  * Who can edit a given action type?
  * Who can apply an action type with a given set of parameters?



## Apply action

The ability to apply an action type depends on the configuration of the object types and link types it is editing. In all cases, the user submitting the action must be able to view the edited object types and link types and their datasources, and pass the [submission criteria](/docs/foundry/action-types/submission-criteria/). If the object type only allows edits via actions, users can make edits for all the objects they can view. For object types and link types allowing edits beyond actions, the user also needs edit permissions on the writeback dataset if the object type or link type is backed by a dataset. If the object type or link type is backed by a [Restricted View](/docs/foundry/security/restricted-views/), the user needs to pass the edit policy.

To review access more broadly, use the **Check access** panel in the workspace sidebar to check a user's access to a Workshop module, including access to dependent action types and their submission criteria. For more information, review the [Check access panel documentation](/docs/foundry/security/checking-permissions/).

Read-time enforcement only

Row and column access controls (including [restricted views](/docs/foundry/security/restricted-views/), [object security policies](/docs/foundry/object-permissioning/object-security-policies/), and [property security policies](/docs/foundry/security/property-security-markings/)) filter what a user can read when invoking an action. These controls do not extend to the action's write. To keep data protected as it flows downstream, pair these controls with a [marking](/docs/foundry/security/markings/) or [Classification-based Access Control](/docs/foundry/security/classification-based-access-controls/). For the full model, see [Access control propagation](/docs/foundry/security/access-control-propagation/).

### Submission criteria

Action submission criteria allow for fine-grained control over who can run an action. Simple submission criteria can require a specific user ID or group ID and can be combined with information from parameters. For more information see the [submission criteria documentation](/docs/foundry/action-types/submission-criteria/).

### Read and write authorizations

Read and write authorizations limit which marked data an action can read directly or receive as input from other logic and enforce minimum security for data it creates or modifies. For more information see the [read and write authorizations documentation](/docs/foundry/action-types/read-write-authorizations/).

### Action log permissions

If an action type is configured with an [action log](/docs/foundry/action-types/action-log/)](/docs/foundry/security/classification-based-access-controls/#conjunctive-and-disjunctive-classification-marking-categories) for details about classification hierarchies and conjunctive or disjunctive categories.

## Configure read and write authorizations

In the action type editor, enable **Access constraints** to configure authorizations. Enabling access constraints initializes both authorizations with no markings. Disabling access constraints clears both authorizations.

Use the **Write security** and **Read security** tabs to configure each authorization separately. The editor displays a warning when the settings differ. If an authorization contains values that you cannot view, those values appear as redacted and cannot be edited.

Before submitting an action, the action form can display **Minimum required security** based on write authorization. The status can be:

  * **Security passed:** The destination security is expected to meet the configured minimum.
  * **Security not passed:** The destination security is below the configured minimum.
  * **Canât be determined:** Foundry cannot determine the destination security before submission.
  * **Not set:** No write authorization markings are configured.
  * **Redacted:** The user cannot view the configured authorization.



The form status provides early feedback. Foundry performs the authoritative validation when the action is submitted and fails the action if the resulting data does not meet write authorization.

[âPREVIOUSPermissions](/docs/foundry/action-types/permissions/)

[NEXTConsistency and isolationâ](/docs/foundry/action-types/consistency-guarantees/)