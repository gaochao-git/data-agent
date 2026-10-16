# Palantir Foundry Docs — /docs/foundry/foundry-rules/legacy-foundry-rules-setup-taurus

> 来源: https://www.palantir.com/docs/foundry/foundry-rules/legacy-foundry-rules-setup-taurus/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Legacy Foundry Rules Setup (Taurus)

Prior to July 2022, Foundry Rules (previously known as Taurus) required additional configuration and used slightly different concepts. The following documentation covers the differences between this and newer versions of Foundry Rules.

## Transforms pipeline

Previously, instead of Foundry Rules generating the **transforms pipeline** automatically, users were required to create and maintain a [Code Repository](/docs/foundry/code-repositories/overview/) that ran the rules.

Learn more about creating and [updating this repository](/docs/foundry/foundry-rules/configure-transforms-pipeline/).

## Workshop application

Instead of the [rule inputs](/docs/foundry/foundry-rules/rule-logic/#inputs) being configured in the [Workflow Configuration Editor](/docs/foundry/foundry-rules/foundry-rules-workflow-configuration/), they were previously required to be configured in both the Workshop application and the transforms pipeline.

Additionally, rule outputs were configured in three locations: the Workshop application, the transforms pipeline, and the [Ontology Manager](/docs/foundry/ontology-manager/overview/). Learn more about legacy rule outputs in the Rule Actions section below.

Learn more about adding and removing rule inputs in the [Workshop application](/docs/foundry/foundry-rules/configure-workshop-app/) and the [transforms pipeline](/docs/foundry/foundry-rules/configure-transforms-pipeline/).

## Rule Actions

Before [workflow outputs](/docs/foundry/foundry-rules/foundry-rules-workflow-configuration/#workflow-outputs), output schema enforcement was achieved with Foundry Actions. The parameters of the Action represent dataset columns and must be mapped to either columns outputted by the logic or static values inputted by the user. The rule Action can be accessed from [within the transform](/docs/foundry/foundry-rules/configure-transforms-pipeline/#rule-action-datasets) to retrieve all matching rows in the specified format. When retrieving the results for a specified rule Action, _the dataset produced will contain the rows output by all rules which use that Action_. This is designed to make it easier to achieve consistency in the output of all Foundry rules. Being Foundry Actions, they must be configured within the Ontology Manager. However, these rule Actions are not executed directly on top of the relevant objects; currently, their only effect is to specify the output schema.

Learn more about [configuring rule Actions](/docs/foundry/foundry-rules/configure-rule-actions/).

Some legacy versions of Foundry Rules (previously known as Taurus) deployed prior to January 2021 need to be upgraded before they can use rule Actions. Unless you have been specifically directed to, you likely do not need to do this. Learn more about [upgrading to use rule Actions](/docs/foundry/foundry-rules/upgrade-to-use-rule-actions/).

[âPREVIOUSTime series / Deploy time series Foundry Rules](/docs/foundry/foundry-rules/deploy-timeseries-foundry-rules/)

[NEXTMigrate to Foundry Rulesâ](/docs/foundry/foundry-rules/migrate-to-foundry-rules/)