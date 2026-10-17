# Palantir Foundry Docs — /docs/foundry/data-integration/reset-stream

> 来源: https://www.palantir.com/docs/foundry/data-integration/reset-stream/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Reset stream

Resetting an ingest stream clears the existing records from a stream and provides an opportunity to change the stream's schema, throughput, and configuration values. This can be helpful during stream pipeline development since you can clear and update an ingest stream without replacing existing references of the stream within your Pipeline Builder pipelines.

Resets are only available for ingest streams. Downstream consuming pipelines of the ingest stream must be replayed after a reset.

- [Push-based ingestion](/docs/foundry/data-connection/push-based-ingestion/) requires updating the POST URL to reference the new stream `viewRid` value.
- [Magritte-based ingestion](/docs/foundry/data-connection/set-up-streaming-sync/) requires an agent rebuild.

Resetting streams can have irreversible effects on data. We do not recommend resetting production ingest streams because existing records will be lost.

To reset a stream, follow the instructions below:

1. Open an ingest stream.
2. Select the **Details** tab.
3. In the **Stream Settings** section, select **Reset stream**.

![Stream connection details.](/docs/resources/foundry/data-integration/reset-stream-open.png)

4. You will be redirected to the stream reset page. On this page, you can optionally update the schema, throughput, or configuration values. If you only want to clear data from the stream, leave those sections unchanged.

![Stream connection reset view.](/docs/resources/foundry/data-integration/reset-stream-page.png)

5. Select **Reset stream** to initiate the stream reset.
