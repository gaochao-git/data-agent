# Palantir Foundry Docs — /docs/foundry/foundry-rules/rule-permissions

> 来源: https://www.palantir.com/docs/foundry/foundry-rules/rule-permissions/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Permissions for editing rules

In some workflows, you may want to restrict the set of users that can edit or modify a rule. The following example shows how you can set up permissions so that a rule's author has final approval for any changes affecting their rule.

The permissioning setup is as follows:

  * Only rule authors (and superusers) can approve proposals that edit their rules.
  * Only rule authors (and superusers) can approve proposals that delete their rules.
  * Only rule authors and proposal creators (and superusers) can reject proposals on the rule owner's rules. 
    * To mitigate the issue of accidental proposals, proposal creators can also reject proposals. For example, if User A accidentally creates a proposal on User B's rule, User A is able to reject (effectively rescinding) that proposal.



Follow the steps below to achieve this permissioning setup:

  1. Configure the _approve a proposal to edit a rule_ Action so that users must either be the rule author associated with the proposal or a superuser.   
  
  
  


  2. Configure the _approve a proposal to delete a rule_ Action so that users must either be the rule author associated with the proposal or a superuser.

  3. Configure the _reject a proposal_ Action so that users must either be the rule author associated with the proposal, the proposal author, or a superuser.   
  
  
  





If you do not see an option to validate based on a rule object, you likely do not have the rule object added as a parameter to the Action. Add a new rule object parameter to the _reject a proposal_ Action, just as you would add rule object parameters of other Foundry Rules actions.

[âPREVIOUSAdd a custom property](/docs/foundry/foundry-rules/add-a-custom-property/)

[NEXTPermitted and default output valuesâ](/docs/foundry/foundry-rules/permitted-and-default-output-values/)