# Palantir Foundry Docs — /docs/foundry/pipeline-builder/management-input-sampling

> 来源: https://www.palantir.com/docs/foundry/pipeline-builder/management-input-sampling/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Add an input sampling strategy

If your input datasets are large, you can speed up preview times by adding a sampling strategy to those inputs.

1. Right-click the input node that you would like to sample, then select **Sampling strategies** in the dropdown menu.

![A dropdown menu with the Sampling strategies option.](/docs/resources/foundry/pipeline-builder/management-sampling-entry-point.png?width=400)

2. From the sampling strategies dialog, select the desired input dataset.

![The sampling strategies dialog.](/docs/resources/foundry/pipeline-builder/management-sampling-dialog.png?width=800)

3. Choose the **Percentage** strategy, and enter a number between 1 and 100 to downsample your input.

![The sampling strategies dialog configured to use a 20% strategy. ](/docs/resources/foundry/pipeline-builder/management-sampling-percentage.png?width=800)

4. Close the dialog. A blue badge should now appear on the top right of your input node, indicating that a sampling strategy has been applied.

![An input node showing a sampling strategies indicator.](/docs/resources/foundry/pipeline-builder/management-sampling-badge.png?width=400)

The preview panel of any nodes downstream of the input will also indicate that sampling was applied.

![Sampling strategies are applied to downstream inputs.](/docs/resources/foundry/pipeline-builder/management-sampling-downstream.png?width=800)

## Filter sampling strategy for file datasets

For file datasets, use a **Filtered preview** sampling strategy to select files based on metadata columns.

To configure a filter sampling strategy:

1. Open the sampling strategies dialog and select a file dataset input. Inputs of different types appear in separate sections of the dialog.
2. Choose **Filtered preview** from the sampling strategy options.
3. Configure your filter conditions based on the available file metadata columns.

If you select an invalid filter sampling strategy for a given input, you will receive error messaging with remediation options.

## Apply a sampling strategy to multiple inputs

In the sampling strategies dialog, select **Apply to all inputs** to set the same strategy for all compatible inputs.

Some inputs may not support certain sampling strategies. When you use **Apply to all inputs**, warnings are displayed for any inputs that do not support the selected strategy.
