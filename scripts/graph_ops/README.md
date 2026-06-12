# Graph Ops Scripts

This directory provides simple operations scripts for Apache Jena Fuseki and GraphDB.

## Files

- `jena_ops.sh`
- `graphdb_ops.sh`

## Jena (Fuseki)

Common commands:

```bash
./scripts/graph_ops/jena_ops.sh start --fuseki-home /opt/apache-jena-fuseki-4.10.0 --dataset zhidu
./scripts/graph_ops/jena_ops.sh status --dataset zhidu
./scripts/graph_ops/jena_ops.sh import --dataset zhidu --ttl /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.ttl
./scripts/graph_ops/jena_ops.sh count --dataset zhidu
./scripts/graph_ops/jena_ops.sh query --dataset zhidu --query-file server/apps/complaint_app/tools/zhidu_clause_extract/examples/conflict_candidates.sparql
./scripts/graph_ops/jena_ops.sh tail-log --dataset zhidu
./scripts/graph_ops/jena_ops.sh stop --dataset zhidu
```

## GraphDB

Common commands:

```bash
./scripts/graph_ops/graphdb_ops.sh start --graphdb-home /opt/graphdb-11.3.2
./scripts/graph_ops/graphdb_ops.sh status
./scripts/graph_ops/graphdb_ops.sh list-repos
./scripts/graph_ops/graphdb_ops.sh import --repo zhidu --ttl /data/aigc/zhidu_0407/output/08_clause_semantics_ontology/clause_semantics_ontology.graphdb_ready.ttl
./scripts/graph_ops/graphdb_ops.sh count --repo zhidu
./scripts/graph_ops/graphdb_ops.sh query --repo zhidu --query "SELECT (COUNT(*) AS ?c) WHERE { ?s ?p ?o }"
./scripts/graph_ops/graphdb_ops.sh tail-log
./scripts/graph_ops/graphdb_ops.sh stop
```

## Notes

- Default Fuseki endpoint format: `http://<host>:3030/<dataset>/query`
- Default GraphDB endpoint format: `http://<host>:7200/repositories/<repo-id>`
- For GraphDB imports, use `clause_semantics_ontology.graphdb_ready.ttl` when your source file contains relative IRIs.
