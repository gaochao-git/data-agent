# Palantir Foundry Docs — /docs/foundry/pipeline-builder/transforms-trained-model

> 来源: https://www.palantir.com/docs/foundry/pipeline-builder/transforms-trained-model/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Trained model node

The trained model node allows you to run user-defined [Machine Learning models](/docs/foundry/model-integration/overview/) — trained either inside or outside of Foundry — directly within a Pipeline Builder pipeline. This enables ML teams and no-code users to seamlessly integrate model inference into their data pipelines without writing any code.

## Getting started

Use the following steps to import a model and configure its inputs and outputs in a batch or streaming pipeline.

### 1. Configure your pipeline

For a batch pipeline, ensure you are using Spark (batch) mode and that **Warm pool** is set to **OFF**. Create a new pipeline if your existing one is not configured to use Spark (batch) mode.

![Check if you are working with a batch pipeline with warm pool disabled.](/docs/resources/foundry/pipeline-builder/pipeline-batch-and-warm-pool-off.png?width=800)

For streaming execution, open or [create a streaming pipeline](/docs/foundry/building-pipelines/create-stream-pipeline-pb/).

![Make sure you are working with a streaming pipeline.](/docs/resources/foundry/pipeline-builder/streaming-model.png?width=800)

### 2. Import your model

Navigate to **Reusables** > **Trained Models** in the import menu and follow the resource import flow to make your model available to the pipeline.

![Trained models import menu.](/docs/resources/foundry/pipeline-builder/model-import.jpg?width=800)

### 3. Add the model node

Select a node in your pipeline canvas and select **Trained model** to insert it.

The **Trained model** option only appears after you have imported at least one model into the pipeline following [step 2](#2-import-your-model).

![Selecting the trained model node on the pipeline canvas.](/docs/resources/foundry/pipeline-builder/add-model-node.jpg?width=800)

### 4. Configure inputs and outputs

Map your input and output columns to the model's expected API schema.

- **Input columns** support expressions, not just direct column mappings. You can apply casts, transformations, and other expressions to your input data before it is passed to the model.
- **Output columns** can be aliased in the result dataset. This allows you to rename model output fields without needing to modify the model itself.

![Input and output column mapping for the trained model node.](/docs/resources/foundry/pipeline-builder/configure-model-inputs-outputs.jpg?width=800)

### 5. Deploy your pipeline

For streaming pipelines, complete the [streaming configuration](#streaming-configuration) before deploying.

Connect the model node to a pipeline output and select **Save**. Select **Deploy**, then **Deploy pipeline**. See [Deliver pipeline](/docs/foundry/pipeline-builder/outputs-deliver-pipeline/) for deployment and build options.

## Streaming configuration

The [model import](#2-import-your-model), [node creation](#3-add-the-model-node), and [input/output mapping](#4-configure-inputs-and-outputs) steps are identical for batch and streaming pipelines. For streaming execution, you should also configure how records are grouped for inference.

### Configure partition keys and windowing

Open **Streaming configuration** in the model node after configuring its inputs and outputs:

1. Choose partition key columns that identify records the model should process together. For example, use a series identifier to keep each time series separate. You can also use partition keys already configured before the model node. See [streaming partition keys](/docs/foundry/building-pipelines/streaming-keys/#partition-keys) for guidance on distributing records across workers.
2. Select a [window type](/docs/foundry/building-pipelines/streaming-stateful-transforms/#windows) and configure its size. Choose a size that supplies enough records for your model while meeting its memory and latency requirements.
3. For event-time windows, add [**Assign timestamps and watermarks**](/docs/foundry/pb-functions-transform/assignTimestampsAndWatermarksV1/) before the model node and set **Timestamp expression** to the column or expression representing when each event occurred. Event-time windows require these timestamps and watermarks to determine window membership and completion. See [Event time and watermarks](/docs/foundry/building-pipelines/streaming-stateful-transforms/#event-time-and-watermarks) for watermark progress, late records, and idle input handling.

![Streaming, input, and output column mapping for the trained model node.](/docs/resources/foundry/pipeline-builder/configure-streaming-model-inputs-outputs-window.png?width=800)

The model runs inference on the records collected in each window for each partition key. Window completion and inference duration both affect output latency. Larger windows can increase the wait for results and the work per inference request.

After deployment, you can inspect records in the output stream after windows complete and inference finishes. See the [streaming pipeline walkthrough](/docs/foundry/building-pipelines/create-stream-pipeline-pb/#part-3-transform-a-stream) for output creation and inspection. Trained-model preview is unavailable for streaming execution.

## Supported models

Currently, only models with a single tabular input and a single tabular output in their [model API](/docs/foundry/integrate-models/model-adapter-api/) are supported. The model must have at least one **required** input column.

When using the model node, your model must return exactly the columns defined in the model's API. Additional columns not defined in the model API will be dropped, whereas columns that are missing may result in build errors.

## Time series in streams

Time series models are supported only in streaming pipelines.

Follow the [streaming configuration](#streaming-configuration) guidance to prepare records for your time series model:

- Choose partition key columns that identify each time series so that records from different series are processed separately.
- Assign timestamps and watermarks using the column or expression that represents when each observation occurred.
- Use event-time windows to group observations by their timestamps. Choose a window size that covers the time interval your model needs for inference, accounting for the expected data frequency, memory requirements, and output latency.
- Map the observation timestamps, measured values, and any other required columns to the model's input schema.

Event-time windows and watermarking do not sort records. If your model requires an ordered sequence, sort its input records by the observation timestamp within the model before inference.

## Supported column types

This feature supports models [whose API](/docs/foundry/integrate-models/model-adapter-api/) includes the following data types for tabular input and output columns:

- **Primitives:** `string`, `boolean`, `integer`, `long`, `float`, `double`, `date`, `timestamp`
- **Complex:** `array`, `map`, `struct` (including nested fields)
- **Media:** `mediaReference`

A `mediaReference` column lets the model read the referenced media item during inference. For details on working with media references in a model adapter, see [Media references](/docs/foundry/integrate-models/model-adapter-reference/#media-references).

Unsupported types such as `objectSet` are rejected at validation time.

## Resource configuration & compute cost

In batch execution, models run as isolated sidecar processes alongside your Spark executors, each with their own dedicated resources. The default resource allocation per model sidecar in both batch and streaming execution is:

| Resource | Default |
| --- | --- |
| CPU | 1 core |
| Memory | 8 GB |
| GPU | None |

If you experience slow batch builds or streaming inference, you can take either or both of the following actions:

1. Increase the [compute profile](/docs/foundry/pipeline-builder/management-build-settings/) for your pipeline.
2. Increase the resources allocated to your model.

To adjust your model's resources, open the **Model import** window (**Reusables > Trained models**) and select **Configure resources**:

![Trained model import window with configure resource options.](/docs/resources/foundry/pipeline-builder/trained-model-import-window.png?width=800)

This opens the configuration panel where you can adjust CPU, memory, and GPU allocation, as well as the model startup timeout override under the **Advanced configuration** section:

![Model configuration dialog with resource allocation and advanced configuration sections.](/docs/resources/foundry/pipeline-builder/trained-model-resource-configuration.png?width=800)

Resource configurations are set per Pipeline Builder pipeline, not per model node. All nodes of the same model within a build/pipeline share the same resource configuration. For streaming pipelines, redeploy the pipeline to apply resource changes.

Keep in mind that this sidecar process isolation comes with additional compute overhead compared to standard transforms. In batch execution, a sidecar is launched on every Spark executor and the driver for each model. In streaming execution, a sidecar is launched on every Flink task manager for each model. The diagram below shows how sidecars scale in batch execution.

![Diagram showing how model sidecars scale across Spark executors and the driver.](/docs/resources/foundry/pipeline-builder/compute-cost-graphic.jpg?width=800)

Streaming task managers and model sidecars consume allocated compute continuously while the pipeline runs, including while waiting for records or window completion. Account for worker parallelism, model resource allocations, and pipeline running time when estimating costs. See [streaming compute usage](/docs/foundry/building-pipelines/streaming-compute-usage/) for guidance.

If a model is imported into a pipeline but not used as a node, no sidecar is spun up and no additional cost is incurred.

## Execution modes

| Execution mode | Supported |
| --- | --- |
| Batch (Spark) | ✅ |
| Streaming | ✅ |
| Faster (Lightweight/DataFusion) | ❌ (planned) |
| Preview | ✅ |

## Auto-upgrades & branching

Pipeline Builder always uses the latest available version of a model on the build's branch. For example, builds on `master` will always use the latest model version published to `master`. If the model version is not found on the build's branch, the system falls back to configured [fallback branches](/docs/foundry/pipeline-builder/branches-fallback-branches/#configure-fallback-branches-in-pipeline-builder), typically the `master` branch unless otherwise configured. This allows machine learning teams to retrain and publish new model versions with confidence that downstream builds will automatically pick up the latest version. You cannot currently pin a model node to a specific version; static version pinning is planned as a future feature.

In streaming execution, a running pipeline continues using its loaded model version. Restart or redeploy the pipeline to update to the latest model version.

## Marketplace

When you package a batch pipeline that uses trained model nodes, the models used as transforms are automatically added as resources to the Marketplace package using the [**Include with content** packaging mode](/docs/foundry/model-integration/marketplace-models/#include-with-content). This means installers receive both the pipeline and its associated models in a single installation — no additional configuration is required to package models.

Alternatively, models in batch pipelines can be configured as [**product inputs**](/docs/foundry/model-integration/marketplace-models/#use-a-model-as-a-product-input) in DevOps, allowing installers to select a model already available on their enrollment rather than receiving the model as packaged content.

Streaming pipelines with trained model nodes cannot be distributed through Marketplace.

For more details on adding Pipeline Builder pipelines to Marketplace products, see [Add pipeline to a Marketplace product](/docs/foundry/pipeline-builder/marketplace-pipeline-builder/).

## Sidecar timeouts

When a batch build or streaming pipeline starts, each model sidecar must download and load the model before it can serve inference requests. By default, the system allows up to 10 minutes for a sidecar to become ready. If it does not become ready within that window — whether because the model is still loading or because the sidecar has died (often due to running out of memory) — execution fails with a timeout error.

If your model requires more (or less) time to load, you can override the default startup timeout. In the **Configure model** dialog, under **Advanced configuration**, set a **Model startup timeout override** value in seconds. Leave the field empty to use the default.

The startup timeout override is configured per model, per pipeline, and applies to every sidecar launched for that model in the batch build or streaming pipeline.

In batch execution, once a sidecar is ready, individual inference requests have **no timeout limit**. This means long-running models — such as those performing complex computations or processing large batches — are not subject to per-request time constraints and will run to completion.

In streaming execution, a long-running inference request delays the corresponding window's output. The startup timeout controls model loading, not inference duration.

[Learn more about training and deploying models in Foundry.](/docs/foundry/model-integration/models/)
