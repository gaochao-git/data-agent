# Logical Dataset

语义层和本体层共用的逻辑数据资产层。

## 边界

- Dataset 只描述逻辑结构、主键、版本、分支、事务、成员数据源绑定和血缘。
- Dataset 不承载指标口径、对象类型、关系类型、Action 定义。
- 语义模型和对象类型都引用 Dataset，二者之间不互相依赖。
- 一个 Dataset 代表一套 schema；物理落地按 member datasource 分别绑定。

## 分层与来源（Pipeline stages）

| 字段 | 取值 | 含义 |
| --- | --- | --- |
| `dataset_layer` | `source` | 从数据源逻辑表直接注册的源 Dataset |
| | `cleaned` | Pipeline 清洗后的 Dataset |
| | `enriched` | Pipeline 加工/聚合后的 Dataset |
| `dataset_origin` | `datasource` | 数据源注册产生 |
| | `pipeline` | Dataset Pipeline build 产生 |

存量 Dataset 默认 `source` / `datasource`。Pipeline 输出 Dataset 固定 `origin=pipeline`，
输入可以是任意未删除 Dataset（支持 source → cleaned → enriched 链式加工）。

## 表

```text
chat_data_logical_dataset
chat_data_dataset_branch
chat_data_dataset_transaction
chat_data_dataset_version
chat_data_dataset_binding
chat_data_dataset_lineage_edge
chat_data_pipeline_definition
chat_data_pipeline_node
chat_data_pipeline_edge
chat_data_pipeline_run
chat_data_ontology_source_binding
```

DDL 见 `server/apps/chat_data_app/next_version.md`，新环境兜底见 `server/utils/schema.py`。

## Pipeline 模型

- 定义使用稳定 `pipeline_id`，`pipeline_name` 为 API 名；每次保存定义追加新的
  `current_version_no`，节点/边按 `(pipeline_id, pipeline_version)` 只追加，不覆盖历史。
- 定义必须是单链：1 个 `source` → N 个 `transform`（至少 1 个）→ 1 个 `output`。
- `source` 节点配置 `dataset_name`（可选 `member_datasource_ids`、`limit`），从 Dataset
  binding 读取物理表数据。
- `transform` 节点配置 `transform_type`，MVP 支持：
  - `filter`：`conditions: [{column, operator, value}]`，operator 取
    `eq/ne/gt/gte/lt/lte/contains`；
  - `aggregate`：`group_by: [列]` + `metrics: [{column, function, alias}]`，
    function 取 `sum/avg/min/max/count`。
- `output` 节点配置 `dataset_name`、`layer`（`cleaned`/`enriched`），可选
  `display_name`/`description`/`primary_key`/`status`。
- 新增 transform 类型：在 `pipeline_transforms.py` 注册并实现 normalize/apply 即可，
  build 编排不需要改。

## Build 语义

`POST /admin/meta/pipelines/{name}/build`：

1. 落 `chat_data_pipeline_run`（`running`，失败也有运行记录）；
2. 读取输入 Dataset 首个 member 的物理表数据（`pipeline_runner`，单测可替换）；
3. 按顺序执行 transform 链，得到输出行与列；
4. 复用 Dataset 版本权威链路写输出 Dataset 的 transaction（带 `run_id`）、不可变
   version（带 `row_count`）与 lineage edge（输入当前版本 → 输出新版本，
   `operation=pipeline_build`）；
5. 运行终态写回 run（`succeeded`/`failed`，输出摘要含行数、列与最多 20 行 preview）。

MVP 输出 Dataset 只有逻辑 schema/统计，没有物理落地表（bindings 为空），
物理物化与增量刷新后续扩展；查询仍以 `chat_data_dataset_version` 为准。

## API

```text
GET    /admin/meta/datasets                     # 支持 dataset_layer/dataset_origin 筛选
GET    /admin/meta/datasets/{name}
PUT    /admin/meta/datasets/{name}
DELETE /admin/meta/datasets/{name}
POST   /admin/meta/datasets/bootstrap
POST   /admin/meta/datasets/{name}/branches

GET    /admin/meta/pipelines
GET    /admin/meta/pipelines/{name}
PUT    /admin/meta/pipelines/{name}
DELETE /admin/meta/pipelines/{name}
POST   /admin/meta/pipelines/{name}/build
GET    /admin/meta/pipelines/{name}/runs
GET    /admin/meta/pipeline-runs/{run_id}
```

本体对象类型通过以下接口绑定：

```text
PUT /admin/meta/ontology/object-types/{name}/source-binding
```

## 版本模型

- 每个 Dataset 默认有 `main` 分支。
- 每次全量提交创建一个不可变版本。
- 提交动作经过 transaction 记录，成功后更新 `current_version_id`。
- `previous_version_id` 存在时写入 lineage edge。
- Pipeline build 是 Dataset 事务的另一种产生方，版本模型与手动提交一致。

## 多租户

对象身份最终必须是：

```text
member_datasource_id + object_type + local_key
```

不同 member 的相同本地主键不是同一个对象；查询、关系、Action 都必须带 member 范围。

## 已知限制

- `physical_table` 仅支持 `table` 或 `schema.table` 两种合法标识符形式；需要
  catalog/schema 限定或函数封装时后续扩展。
- Pipeline 输出暂未物化到物理表，输出 Dataset 的 `bindings` 为空。
- MVP 只支持手动 build，无调度/增量/失败重试。
