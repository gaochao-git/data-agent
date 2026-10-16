# Palantir Foundry Docs — /docs/foundry/vulcan/overview

> 来源: https://www.palantir.com/docs/foundry/vulcan/overview/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Vulcan

**Vulcan** is a 3D rendering application that displays computer-aided design (CAD) models, engineering drawings, and point clouds in the Palantir platform.

With Vulcan, you can:

  * View CAD models stored in the [Ontology](/docs/foundry/ontology/overview/) as CAD objects. See [Schema reference](/docs/foundry/vulcan/schema-reference/) for the object types and properties that Vulcan requires.
  * Annotate, measure, section, and recolor parts of a model. See [Tools](/docs/foundry/vulcan/tool-overview/).
  * Embed the 3D viewport in a Workshop module. See [Embed Vulcan in a Workshop module](/docs/foundry/vulcan/widget/).



Annotations, measurements, and section planes saved from Vulcan are Ontology objects, so they can drive other Palantir platform workflows such as alerts and dashboards.

_This screenshot uses notional data._

## Getting started

Vulcan renders 3D models that are stored in the [Ontology](/docs/foundry/ontology/overview/) as CAD objects. A [CAD object](/docs/foundry/vulcan/schema-reference/#cad-object) is an Ontology object with a property carrying the `cad_media_reference` type class, which holds a media reference to a model file.

In the standalone application, select **Add to scene** , then choose a CAD object. Repeat this process to add more models to the scene.

In the Vulcan widget, the widget configuration determines which models load. See [Choose a CAD object](/docs/foundry/vulcan/widget/#choose-a-cad-object).

### Supported model formats

Vulcan renders three kinds of geometry: mesh models, which approximate the surface of each part with triangles; 2D engineering drawings; and point clouds. You can point a CAD object's media reference property (the property carrying the `cad_media_reference` type class) at a file in any of the following formats without conversion:

  * Mesh formats: `GLB`, `GLTF`, `STL`, `OBJ`, `PLY`, `3MF`
  * Engineering drawings: `DXF`
  * Point clouds: `LAS`, `LAZ`



[âPREVIOUSMap / Add Map templates to a Marketplace product](/docs/foundry/map/marketplace-map-templates/)

[NEXTToolsâ](/docs/foundry/vulcan/tool-overview/)