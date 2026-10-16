# Palantir Foundry Docs — /docs/foundry/vulcan/widget

> 来源: https://www.palantir.com/docs/foundry/vulcan/widget/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Embed Vulcan in a Workshop module

You can embed Vulcan in a Workshop module by using the **Vulcan** widget. The widget provides the same 3D viewport and tools as the standalone application. Its inputs, outputs, and interface can also connect to Workshop variables so that Vulcan can interact with other widgets in the module.

Embedding Vulcan in a Workshop module takes three steps: add the widget, choose a CAD object, and configure the widget.

## Add a Vulcan widget in Workshop

In the Workshop section where you want Vulcan to appear, select the **+** icon or **\+ Add widget** to open the widget menu, then search for and select the **Vulcan** widget.

## Choose a CAD object

Vulcan needs at least one CAD object to render. A CAD object is an Ontology object with a property carrying the `cad_media_reference` type class. For the complete schema, refer to [CAD object](/docs/foundry/vulcan/schema-reference/#cad-object).

Under **Input > Models**, add a model and set **Model objects** to a Workshop object set that contains the CAD object. Vulcan loads the first object in the set. To render multiple models, add a separate model entry for each object set.

## Configure the widget

The Vulcan widget configuration is organized into the following sections.

### Input

The **Input** section controls the Ontology data and scenario that the widget uses:

  * **Models:** A repeatable list of CAD model inputs. 
    * **Model objects:** The object set containing the model.
    * **Options:** Settings that apply only to this model. **Transforms** and **Part coloring** accept function outputs described in the return schemas below. 
      * **Annotation objects:** An object set of annotations associated with the model. Each object must follow the [annotation schema](/docs/foundry/vulcan/schema-reference/#annotation).
      * **Transforms:** Transform overrides for part instances or assemblies. Refer to Transforms return schema.
      * **Selected part ID** and **Selected part instance ID:** Two-way string variables for the current selection. A part ID selects every instance of that part in this model; a part instance ID selects one specific instance.
      * **Part coloring:** Color and opacity overrides. Refer to Part coloring return schema.
      * **Fallback color:** A CSS color value for parts in this model that have no assigned color. This value overrides the global **Fallback color** for the model.
  * **Measurement objects** and **Section plane objects:** Object sets containing measurements to display and section planes to apply. Objects must follow the corresponding [measurement](/docs/foundry/vulcan/schema-reference/#measurement) or [section plane](/docs/foundry/vulcan/schema-reference/#section-plane) schema.
  * **Scenario:** The Workshop primary scenario to use when loading Ontology objects.



#### Transforms return schema

**Transforms** accepts a list of structs with the following fields:

Field| Required| Type| Description  
---|---|---|---  
`id`| Yes| String| The part instance or assembly ID to transform.  
`transform`| Yes| Array of numbers| Exactly 16 finite numbers representing a 4Ã4 transformation matrix in column-major order.  
  
The matrix values must use the order `[m11, m21, m31, m41, m12, m22, m32, m42, m13, m23, m33, m43, m14, m24, m34, m44]`. Vulcan ignores entries with a missing ID or an invalid matrix.

#### Part coloring return schema

**Part coloring** accepts a list of structs with the following fields:

Field| Required| Type| Description  
---|---|---|---  
`partInstanceIds`| Yes| Array of strings| One or more part instance or assembly IDs to style.  
`color`| No| String| A CSS color value, such as `#0055ff` or `rgb(0, 85, 255)`.  
`opacity`| No| Number| Base opacity, from `0` for transparent to `1` for opaque.  
`selectedOpacity`| No| Number| Opacity while selected or highlighted, using the same `0` to `1` scale.  
  
Each struct must define at least one of `color`, `opacity`, or `selectedOpacity` to affect the model. If the Scene Coloring tool also assigns a color, the scene coloring value takes precedence while the configured opacity values remain in effect.

### Interaction

Camera target, position, and zoom bindings work in both directions. The **Interaction** section connects the following camera and scene interaction state to Workshop variables:

  * **Camera target X / Y / Z:** The point that the camera faces. Configure all three coordinates.
  * **Camera position X / Y / Z:** The camera location. Configure all three coordinates.
  * **Camera zoom:** The camera zoom level.
  * **Click X / Y / Z:** Output number variables for the 3D coordinates of the point on a model part that the user most recently selected. Bind all three variables to reconstruct the selected point.



### Styling

Color fields accept CSS color values. The **Styling** section controls the default scene appearance with the following settings:

  * **Fallback color:** The color for parts that have no assigned color. A model-specific fallback color takes precedence for that model.
  * **Background color:** The 3D viewport background color.



### Interface

All options are on by default. The **Interface** section controls which interface elements are available:

  * **Show sidebar** , **Show toolbar** , and **Show gizmo:** Control the visibility of the scene tree sidebar, top toolbar, and orientation gizmo.
  * **Enable context menu:** Allows users to open context menus on parts.
  * **Expand sidebar by default:** Opens the sidebar when the widget first loads.



### Scene

By default, Vulcan uses Complete mode, an Orthographic camera, and Orbit controls. Mode switching and part information are on; auto-rotate is off. The **Scene** section controls how Vulcan presents and navigates the model with the following settings:

  * **Default mode:** The initial scene mode. 
    * **Complete:** Display the assembled model and complete scene tree.
    * **Grid:** Display a sortable grid of unique parts.
    * **Component:** Focus on the selected component.
  * **Enable switching modes:** Show controls for changing scene modes.
  * **Camera mode:** Use an **Orthographic** or **Perspective** camera.
  * **Controls mode:** Use **Orbit** or **Trackball** camera controls.
  * **Enable scene auto-rotate:** Rotate the scene automatically.
  * **Complete mode config > Enable part info on select:** Show part information when a user selects a part in Complete mode.



### Tools

The **Tools** section lets you hide functionality that the module does not need. All settings are on by default. Turn off a setting to remove that tool's controls from the embedded widget. Each tool is described in detail on the [Tools](/docs/foundry/vulcan/tool-overview/) page.

Setting| Functionality  
---|---  
**Enable annotation tool**|  Annotation creation on model parts.  
**Enable mesh styling tool**|  Appearance controls for individual meshes.  
**Enable measurement tool**|  Distance measurements between model points.  
**Enable section view tool**|  Model cross-sections using section planes.  
**Enable transform tool**|  Repositioning of parts and assemblies.  
**Enable scene coloring tool**|  Property- or function-based coloring of parts.  
  
### Actions

The **Actions** section specifies which Ontology actions save user-created content. Use **Annotation action** , **Measurement action** , and **Section plane action** to select the action for each content type.

If you do not configure an action, Vulcan discovers actions that have the required parameter type classes and lets the user choose from the matches. Configuring an action limits saving to that action and can provide defaults or user input fields for additional action parameters. Vulcan supplies the tool-specific values listed in [Action types](/docs/foundry/vulcan/schema-reference/#action-types).

### Output

When at least one output is configured, users can choose which measurement to send to Workshop from the Measurement tool. The **Output** section sends a selected measurement to Workshop variables through the following settings:

  * **Measurement scene distance:** The total measurement distance as a number in the model's scene units.
  * **Measurement unit distance:** The same distance as a formatted string in the user's selected display unit, such as `12.30 mm`. If the model has no recognized unit, the output uses `units`.



[âPREVIOUSSchema reference](/docs/foundry/vulcan/schema-reference/)

[NEXTDynamic Scheduling / Overviewâ](/docs/foundry/dynamic-scheduling/scheduling-overview/)