# Palantir Foundry Docs — /docs/foundry/quiver/card-expression

> 来源: https://www.palantir.com/docs/foundry/quiver/card-expression/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Expression

Use the expression language to derive new columns or perform complex filtering. Advanced features available only in this card, such as [window functions](/docs/foundry/contour/expressions-syntax/) can be used to unlock new types of analysis in Quiver. To use, select either an object set, dataset card, or another Expression card.

![Expression configuration panel](/docs/resources/foundry/quiver/expression-panel.png?width=400)

Select **Additional configuration...** to access **Add new column**, **Replace column**, **Filter**, and **Aggregate** options.

![Expression additional configuration panel](/docs/resources/foundry/quiver/expression-panel-additional.png?width=550)

Where [AIP is enabled](/docs/foundry/aip/enable-aip-features/), you can select the **AIP Configure** option in the Expression card to create expressions using natural language.

![Natural language prompt input in AIP window](/docs/resources/foundry/quiver/quiver-aip-generated-expression.png)

Some sample prompts you can use include:

- “Compute a new column called 'total user score' by multiplying the two score columns”
- “Compute the 'total user score' defined by multiplying the two score columns for each organic category”
- “Compute the average sustainability score for each organic category”
- “Update the values in the Organic column to camel case”
- “Concatenate the two product name columns into one of them”
- “Compute the total revenue per product for each store, taking into account price and quantity sold”

AIP provides a suggestion which you can **Apply**.

![AIP suggestion](/docs/resources/foundry/quiver/quiver-aip-generated-expression2.png)

## Input type

Object set, Dataset

## Output type

Dataset

## Usage information

| Functionality | Availability |
| --- | --- |
| [Standard Quiver card](/docs/foundry/quiver/core-concepts/#cards) | Supported |
| [Transform table transform](/docs/foundry/quiver/cards-transform-table/) | Unsupported |
