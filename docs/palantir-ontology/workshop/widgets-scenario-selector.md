# Palantir Foundry Docs — /docs/foundry/workshop/widgets-scenario-selector

> 来源: https://www.palantir.com/docs/foundry/workshop/widgets-scenario-selector/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Widget: Scenario Selector

The **Scenario selector** widget in Workshop allows a user to select a Scenario(s) from a list of Scenarios and output an active Scenario or Scenario array. Module builders configuring a Scenario Selector widget can enable user selection of separate single Scenarios or Scenario arrays either for comparison or to enable selection without giving users the ability to create Scenarios.

The screenshot below shows an example of a multi-select configuration of the Scenario selector. When placed in a section header, the widget becomes a dropdown instead of a panel.

![scenario_selector_example](/docs/resources/foundry/workshop/scenario-selector-overview.png)

## Configuration Options

Here is a screenshot of the initial state of a newly added Scenario Selector widget alongside its initial configuration panel:

![scenario_selector_example](/docs/resources/foundry/workshop/scenario-selector-config.png)

For the Scenario Selector widget, the core configuration options are the following:

- **Input data**
  - **Candidate Scenarios:** Select the appropriate Scenario array variable or list of Scenario variables to pull Scenarios into your selector.
  - **Output Scenario:** Choose between returning a Scenario array using multi-select or a single Scenario using single-select.
