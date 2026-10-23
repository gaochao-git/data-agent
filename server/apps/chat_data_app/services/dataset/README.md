# Logical Dataset

语义层和本体层共用的逻辑数据资产层。

## 边界

- Dataset 只描述逻辑结构、主键、版本、分支、事务、成员数据源绑定和血缘。
- Dataset 不承载指标口径、对象类型、关系类型、Action 定义。
- 语义模型和对象类型都引用 Dataset，二者之间不互相依赖。
- 一个 Dataset 代表一套 schema；物理落地按 member datasource 分别绑定。

## 表

```text
chat_data_logical_dataset
chat_data_dataset_branch
chat_data_dataset_transaction
chat_data_dataset_version
chat_data_dataset_binding
chat_data_dataset_lineage_edge
chat_data_ontology_source_binding
```

DDL 见 `server/apps/chat_data_app/next_version.md`，新环境兜底见 `server/utils/schema.py`。

## API

```text
GET    /admin/meta/datasets
GET    /admin/meta/datasets/{name}
PUT    /admin/meta/datasets/{name}
DELETE /admin/meta/datasets/{name}
POST   /admin/meta/datasets/bootstrap
POST   /admin/meta/datasets/{name}/branches
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

## 多租户

对象身份最终必须是：

```text
member_datasource_id + object_type + local_key
```

不同 member 的相同本地主键不是同一个对象；查询、关系、Action 都必须带 member 范围。
