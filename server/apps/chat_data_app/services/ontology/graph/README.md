# ontology/graph

NebulaGraph 只读图查询层：多跳关系与路径查询。

## 边界

```text
图库只存对象身份 / 标题 / 关系边
对象属性、指标、Action 仍走 SQL/语义层
```

不在这里做：

```text
Hive 全量导入
对象属性主存储
Action 写回
前端直连 Nebula
```

## 模块

- `contracts.py`：稳定的节点/边/结果契约；
- `service.py`：nGQL 构造、member 范围强制、路径解析；
- `sync.py`：对象投影 → 节点/边 → Nebula，手动/任务同步入口；
- `__init__.py`：对外导出。

## 配置

```text
CHAT_DATA_NEBULA_ENABLED=false
CHAT_DATA_NEBULA_HOSTS=127.0.0.1:9669
CHAT_DATA_NEBULA_USER=root
CHAT_DATA_NEBULA_PASSWORD=nebula
CHAT_DATA_NEBULA_SPACE=chatdata_graph
CHAT_DATA_NEBULA_EDGE_TYPES=belongs_to
CHAT_DATA_NEBULA_TIMEOUT_SECONDS=10
```

默认关闭；未部署 Nebula 的实例不会受影响。

## DDL

已有 space 需要先给通用边加关系类型属性（新 space 可在建边时直接带上）：

```ngql
ALTER EDGE belongs_to ADD (relation_type string DEFAULT "");
```

同步写入：

```text
INSERT EDGE belongs_to(member_id, relation_type) ...
```

## 同步接口

`POST /admin/meta/ontology/graph/sync`

```json
{
  "member_datasource_id": "ORG_CODE1",
  "datasource_group_id": "ds_20260116115851_95505",
  "limit_per_type": 100
}
```

MVP 限制：

```text
每个对象类型最多 100 个对象
逐对象读 links
用于小规模验证，不是全量生产同步
```

生产同步仍应改为批量 SQL 抽取边，见设计文档阶段 1。

## 前端接入

`ObjectInstanceExplorer` 的「图探索」：

```text
选中对象 -> POST /admin/meta/ontology/graph/traverse
depth=2, direction=both, limit=30
```

图不可用/空结果时回退 `read_object_projection` 的一跳 links，页面不白屏。

## Agent 接入

`ontology_tool` 增加：

```text
traverse_objects  1~3 跳关系遍历
find_graph_paths  两个对象之间的有界路径
```

只返回对象身份/关系；需要属性时回源 `get_object`。

## 约束

```text
depth <= 3
limit <= 200
必须指定且只能指定一个 member
```

## 验证

PoC 使用：

```text
NebulaGraph nightly 单机
space=chatdata_graph
Tag=Object(object_type, member_id, title)
Edge=belongs_to(member_id)
```

已验证：

```text
客户 -> 订单 一跳
客户 -> 订单 -> 运单 两跳
FIND PATH 路径
REVERSELY / BIDIRECT
Python nebula3 driver
```

已补手动同步：

```text
POST /admin/meta/ontology/graph/sync
services/ontology/graph/sync.py
```

实测从项目对象投影同步（limit_per_type=1）：

```text
6 对象类型
22 节点
18 边
0 错误
```

生产前仍需补：定时/增量同步、tombstone、批量边抽取、Nebula 集群、监控、稳定版本镜像。
