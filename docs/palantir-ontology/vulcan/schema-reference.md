# Palantir Foundry Docs — /docs/foundry/vulcan/schema-reference

> 来源: https://www.palantir.com/docs/foundry/vulcan/schema-reference/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Schema reference

Vulcan expects a specific set of Ontology object types, actions, link types, and property type classes to be present in your enrollment. This page lists every type class that Vulcan reads or writes.

Create the object types, actions, and link types in [Ontology Manager](/docs/foundry/ontology-manager/overview/), then attach the type classes listed on this page to their properties, action parameters, and link types.

Schema stability

Schema details may evolve as Vulcan develops. Verify your schema against this page after each Vulcan release.

A type class is a label you attach to a property to tell Vulcan what that property is for. Vulcan looks up properties by their type classes, never by name, so you can name properties to fit your own Ontology conventions. A single property can also serve Vulcan and other applications by carrying more than one type class.

All type classes listed below are in the `vulcan` namespace â that is, their `kind` field is always `"vulcan"` and only the `name` field varies.

In the tables below, **Required** indicates that the corresponding type class must be present in the configured schema. Each kind of table adds a further requirement:

  * **Object properties:** Required properties must also have values on stored objects.
  * **Action parameters:** The action must expose a parameter with the listed type class and data type so that Vulcan can supply its value.
  * **Link types:** The link type must carry the listed type class so that Vulcan can traverse it.



## Object types

Vulcan reads and writes the following object types. Each table lists the type classes that Vulcan looks for on that object type's properties.

### CAD object

Represents a 3D model that Vulcan can render. The model geometry is stored as a media reference.

Type class (`name`)| Required| Data type| Description  
---|---|---|---  
`cad_media_reference`| Yes| Media reference| The CAD model file. This property accepts any of the formats listed in [Supported model formats](/docs/foundry/vulcan/overview/#supported-model-formats). Vulcan detects the format from the file extension.  
`up_axis`| No| String| The up-axis convention of the model. Accepted values are `X`, `Y`, and `Z`. If omitted, Vulcan applies a format-specific default: `Y` for `GLB` and `GLTF`, `Z` for `STL`, `OBJ`, `PLY`, `3MF`, `DXF`, `LAS`, and `LAZ`.  
`model_unit`| No| String| How Vulcan interprets the model's coordinates when the file declares no unit of its own. Set this property for `OBJ`, `STL`, `PLY`, `DXF`, `LAS`, and `LAZ`, which never declare a unit. This property has no effect on `GLB`, `GLTF`, or `3MF`, because Vulcan always reads the unit those formats declare. Accepted values are `m` (or `meter` / `metre`) and `mm` (or `millimeter` / `millimetre`). Vulcan ignores any other value.  
  
### Annotation

Represents an annotation attached to a part of a CAD model. Annotations combine a text label, a 3D attachment point, and metadata about authorship and intent.

Type class (`name`)| Required| Data type| Description  
---|---|---|---  
`annotation_id`| Yes| String| Unique identifier for the annotation.  
`annotation_title`| Yes| String| Short label displayed with the annotation.  
`annotation_content`| Yes| String| Longer body text.  
`part_instance_id`| Yes| String| Identifier of the part instance that the annotation is attached to.  
`annotation_attachment_x`| Yes| Double| X-coordinate of the attachment point, in model units.  
`annotation_attachment_y`| Yes| Double| Y-coordinate of the attachment point, in model units.  
`annotation_attachment_z`| Yes| Double| Z-coordinate of the attachment point, in model units.  
`annotation_sphere_size`| Yes| Double| Marker radius in model units. Use `0` to render a point marker.  
`annotation_created_by`| Yes| String| Identifier of the user who created the annotation.  
`annotation_intent`| Yes| String| Visual intent of the annotation marker. See Intent values below.  
`annotation_timestamp`| Yes| Timestamp| When the annotation was created.  
  
#### Intent values

The `annotation_intent` property accepts one of the following string values. Vulcan renders each value as a distinct marker color, so you can encode severity or category in a way your users recognize at a glance. Vulcan does not enforce semantic meaning for these values, so you can set them to match your workflow.

Value| Marker color  
---|---  
`none`| Gray  
`primary`| Blue (the default when a new annotation is created)  
`success`| Green  
`warning`| Yellow  
`danger`| Red  
  
### Measurement

Represents a distance measurement captured between two or more points on a model.

Type class (`name`)| Required| Data type| Description  
---|---|---|---  
`measurement_id`| Yes| String| Unique identifier for the measurement.  
`measurement_points`| Yes| Array of doubles| Ordered 3D point coordinates that define the measurement, serialized as a flat number array. See Points format below.  
`measurement_created_by`| Yes| String| Identifier of the user who created the measurement.  
`measurement_timestamp`| Yes| Timestamp| When the measurement was created.  
  
#### Points format

Measurement points are serialized as a flat array of numbers in the order `[x1, y1, z1, x2, y2, z2, ...]`, expressed in the model's coordinate space. A measurement with two points therefore contains six numbers; a measurement with three points contains nine. Points are interpreted as an ordered polyline â the total distance that Vulcan reports for a measurement is the sum of distances between consecutive points.

### Section plane

Represents a cross-section plane through a model.

Type class (`name`)| Required| Data type| Description  
---|---|---|---  
`section_plane_id`| Yes| String| Unique identifier for the section plane.  
`section_plane_position`| Yes| Array of doubles| Exactly three position values in `[x, y, z]` order.  
`section_plane_quaternion`| Yes| Array of doubles| Exactly four quaternion values in `[x, y, z, w]` order.  
`section_plane_created_by`| Yes| String| Identifier of the user who created the section plane.  
`section_plane_timestamp`| Yes| Timestamp| When the section plane was created.  
  
## Action types

Vulcan writes annotations, measurements, and section planes back to the Ontology through actions. Each action type is identified by the set of parameter type classes it carries.

The tables below list only the parameters that Vulcan supplies. Your action definition must populate every other required property on the created object:

  * Object ID and creator properties, for all three actions. Set the creator property â `annotation_created_by`, `measurement_created_by`, or `section_plane_created_by` â to the user who submitted the action.
  * Timestamp properties, for the **Create measurement** and **Create section plane** actions. Vulcan supplies `annotation_timestamp` only for **Create annotation**.



Additional action parameters do not need Vulcan type classes. When you configure an action in the [Workshop widget](/docs/foundry/vulcan/widget/#actions), you can provide defaults for these parameters or expose them as user input fields.

### Create annotation

Parameter type class (`name`)| Required| Data type| Description  
---|---|---|---  
`annotation_title`| Yes| String| Short label.  
`annotation_content`| Yes| String| Body text.  
`part_instance_id`| Yes| String| Part instance that the annotation attaches to.  
`annotation_attachment_x`| Yes| Double| X-coordinate of the attachment point.  
`annotation_attachment_y`| Yes| Double| Y-coordinate of the attachment point.  
`annotation_attachment_z`| Yes| Double| Z-coordinate of the attachment point.  
`annotation_sphere_size`| Yes| Double| Marker radius in model units. Use `0` for a point marker.  
`annotation_intent`| Yes| String| Category or severity.  
`annotation_timestamp`| Yes| Timestamp| Creation timestamp.  
`cad_object`| Yes| Object reference| Reference to the CAD object that the annotation belongs to.  
  
### Create measurement

Parameter type class (`name`)| Required| Data type| Description  
---|---|---|---  
`measurement_points`| Yes| Array of doubles| Ordered point coordinates in `[x1, y1, z1, x2, y2, z2, ...]` format.  
  
### Create section plane

Parameter type class (`name`)| Required| Data type| Description  
---|---|---|---  
`section_plane_position`| Yes| Array of doubles| Exactly three position values in `[x, y, z]` order.  
`section_plane_quaternion`| Yes| Array of doubles| Exactly four quaternion values in `[x, y, z, w]` order.  
  
## Link types

Type class (`name`)| Required| Description  
---|---|---  
`cad_object`| Yes| Associates an annotation with its parent CAD object.  
  
[âPREVIOUSTools](/docs/foundry/vulcan/tool-overview/)

[NEXTEmbed Vulcan in a Workshop moduleâ](/docs/foundry/vulcan/widget/)