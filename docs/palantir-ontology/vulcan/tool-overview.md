# Palantir Foundry Docs — /docs/foundry/vulcan/tool-overview

> 来源: https://www.palantir.com/docs/foundry/vulcan/tool-overview/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Tools

Vulcan provides tools for annotating, measuring, sectioning, styling, coloring, and repositioning a CAD model. Annotations, measurements, and section planes can be saved as Ontology objects, while function outputs drive part coloring and transforms. Each tool can be shown or hidden when Vulcan is [embedded in a Workshop module](/docs/foundry/vulcan/widget/#tools).

_All screenshots use notional data._

## Annotations

**Annotations** mark a specific point on a part with a label, body text, and a color-coded intent. Use them to flag:

  * Design defects that require a review
  * Fault locations for repair
  * Points of interest for quality checks



## Measurements

**Measurements** capture distances between points on a model and provide a sense of physical scale. A measurement is an ordered set of points, and Vulcan reports the total distance along the polyline they define.

When a model format defines physical units, Vulcan uses them by default. Scene units are the raw, dimensionless coordinate values stored in the model file. For unit-agnostic formats, Vulcan reports distances in scene units unless the [CAD object](/docs/foundry/vulcan/schema-reference/#cad-object) has a property carrying the `model_unit` type class. When neither the file nor the CAD object supplies a unit, you can set one for the current session with **Scene units** in **Measurement settings**. This setting appears only in that case, and it resets when you reload the page.

## Section planes

A **section plane** is a flat plane positioned in 3D space. It hides geometry on one side so that you can see inside an assembly. Use section planes to inspect internal components without hiding individual parts. For example, you can verify a bearing's position inside a housing or review routing paths through a machine frame.

A saved section plane stores its position and orientation so that the cross-section can be recalled later or shared across sessions and users.

## Mesh style

**Mesh style** quickly changes how model geometry is rendered. You can switch between **Normal** , **X-Ray** , and **Wireframe** views.

## Part coloring

**Part coloring** uses property or function outputs to drive **Scene coloring** , providing visual context for data such as:

  * Fault rates
  * Procurement lead times and stock levels
  * Cost



## Part transforms

**Part transforms** use function outputs to reposition parts or assemblies in space. Use transforms to demonstrate:

  * How parts fit together
  * How service equipment must be positioned to reach a part during installation or repair



[âPREVIOUSOverview](/docs/foundry/vulcan/overview/)

[NEXTSchema referenceâ](/docs/foundry/vulcan/schema-reference/)