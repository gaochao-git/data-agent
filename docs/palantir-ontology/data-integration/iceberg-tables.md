# Palantir Foundry Docs — /docs/foundry/data-integration/iceberg-tables

> 来源: https://www.palantir.com/docs/foundry/data-integration/iceberg-tables/
> 抓取: 2026-10-17（本地参考副本，版权属 Palantir）

---

# Iceberg tables

[Apache Iceberg ↗](https://iceberg.apache.org/) is an open-source *table format*. A table format is a specification for how a collection of data files in storage is organized so that many different tools can read and write those files as one table, with a defined set of columns and a history of changes. Reading an Iceberg table typically also uses an *Iceberg catalog*, which records which tables exist and where each table's files are currently stored, so that a tool can find a table without tracking its individual files.

In Foundry, **Iceberg tables** are an alternative resource type to a Foundry [dataset](/docs/foundry/data-integration/datasets/) for representing tabular data. Foundry offers Iceberg both as managed tables via Foundry's Iceberg catalog and as [virtual tables](/docs/foundry/data-integration/virtual-tables/) via an external catalog such as AWS Glue, Snowflake Horizon Catalog, Apache Polaris, or Databricks Unity Catalog. Refer to the [Iceberg tables](/docs/foundry/iceberg/overview/) section of the documentation for more details on Foundry Iceberg tables.
