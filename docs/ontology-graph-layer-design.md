# 本体图查询层接入设计（NebulaGraph PoC）

## 1. 结论先行

```text
NebulaGraph 只做“关系图读模型”
不做对象主存储
不做属性主存储
不做写回
```

对象属性、指标、Action 仍走现有 SQL 投影与语义层；图库只回答：

```text
这个对象和谁有关
N 跳内能到哪些对象
两个对象之间有没有路径
```

## 2. PoC 环境

本机已启动单机 Nebula：

```text
compose：/tmp/nebula-docker-compose/docker-compose-lite.yaml
镜像：vesoft/nebula-{metad,storaged,graphd,console}:nightly
graphd：127.0.0.1:9669
metad：127.0.0.1:9559
storaged：127.0.0.1:9779
```

已建空间：

```text
chatdata_graph
```

已建 schema：

```text
Tag：Object(object_type, member_id, title)
Edge：belongs_to(member_id)
```

已验证：

```text
客户 -> 订单 -> 运单 多跳
FIND ALL PATH 路径查询
Python nebula3 driver 连通
```

PoC 仅证明技术可行，不代表生产选型。

## 3. 数据模型

### 3.1 顶点

```text
VID = 现有对象身份
      member_datasource_id:object_type:local_key
Tag = Object
属性：
  object_type
  member_id
  title
  updated_at
```

不在图里存完整业务属性。

原因：

```text
属性会变
图同步有延迟
权限/脱敏在 SQL/语义层已有
避免双主数据
```

对象详情仍通过 `object_projection.read_object_projection` 回源。

### 3.2 边

第一阶段建议：

```text
Edge = belongs_to
属性：
  relation_type
  member_id
  updated_at
```

或者按关系类型建边：

```text
order_belongs_to_customer
shipment_belongs_to_order
```

推荐第一阶段用统一边 + `relation_type` 属性，避免动态 DDL；关系类型稳定后再拆专用边类型。

### 3.3 方向

保留源库方向：

```text
from_object -> to_object
```

查询时使用 Nebula 的 `REVERSELY` / `BIDIRECT`，不要为反向再存一条边。

### 3.4 反向与双向查询

Nebula 原生支持方向，不需要额外存反向边：

```text
GO ... OVER belongs_to REVERSELY   反向（客户 -> 订单）
GO ... OVER belongs_to BIDIRECT    双向
MATCH (a)-[:belongs_to]-(b)        无向匹配
FIND PATH ... OVER belongs_to BIDIRECT
```

接入契约：

```text
direction = out | in | both
out  = 按 from -> to 方向
in   = REVERSELY
both = BIDIRECT
```

约束：

```text
反向查询必须仍带 member 过滤
深度 <= 3
limit <= 200
双向多跳可能放大结果，必须显式限制
```

## 4. 同步链路

```text
Hive / 业务库
  → 现有 Pipeline（Source → Cleaned → Enriched）
  → 对象身份 + 关系边抽取
  → nodes.csv / edges.csv
  → Nebula Importer 或 nebula3 批量写入
```

关键点：

```text
只抽 PK/FK/关系列
不抽全表
按 member_datasource_id 分批
按 updated_at 增量
删除用 tombstone，不物理删
```

同步任务独立于查询请求：

```text
失败不影响 SQL 问数
图延迟通过 graph_as_of 暴露
```

## 5. 服务层接入

新增：

```text
services/ontology/graph/
  contracts.py       图查询入参/出参
  sync.py            节点/边同步
  query.py           多跳/路径查询
  nebula_adapter.py  nebula3 客户端
```

不直接改 `object_projection` 的 SQL 主路径。

### 查询接口

```python
async def traverse_objects(
    object_key: str,
    *,
    relation_types: list[str] | None,
    direction: Literal["out", "in", "both"] = "both",
    depth: int = 1,
    limit: int = 100,
    member_datasource_ids: list[str] | None = None,
) -> GraphResult: ...
```

```python
async def find_paths(
    from_object_key: str,
    to_object_key: str,
    *,
    max_depth: int = 3,
    limit: int = 10,
) -> GraphResult: ...
```

### 返回结构

```json
{
  "nodes": [
    {
      "object_key": "ORG_CODE1:sales_order:5001",
      "object_type": "sales_order",
      "title": "SO-5001"
    }
  ],
  "edges": [
    {
      "from": "ORG_CODE1:sales_order:5001",
      "to": "ORG_CODE1:customer:1000001",
      "relation_type": "order_belongs_to_customer"
    }
  ],
  "paths": [],
  "graph_as_of": "2026-10-24T09:30:00Z",
  "degraded_reason": ""
}
```

## 6. Agent 工具接入

在现有 `ontology_tool` 增加两个 action，而不是新开一个工具：

```text
traverse_objects
find_paths
```

路由规则：

```text
一跳对象关系       → 现有 SQL search_around
多跳/路径/关系网络 → graph traverse_objects / find_paths
对象属性/指标      → 现有 SQL / 语义层
```

图查询只返回对象身份和路径；需要展示属性时，再调用：

```text
read_object_projection
```

避免图数据过期导致属性错误。

## 7. 权限与多机构

图查询入口必须强制：

```text
member_datasource_id
```

规则：

```text
每个顶点带 member_id
每条边带 member_id
查询必须带 member 过滤
不允许全局图查询
```

禁止把 Nebula 直接暴露给 Agent 或前端。

权限判断仍在：

```text
routers → services
```

图适配器只接收已授权的 member scope。

## 8. 降级与一致性

```text
Nebula 不可用
  → 回退一跳 SQL search_around
  → 返回 degraded_reason

图查询超时
  → 限制 depth ≤ 3，limit ≤ 200
  → 超时降级

图数据滞后
  → 返回 graph_as_of
  → 属性重新回源
```

图查询失败不能让本体模式整体不可用。

## 9. 不建议的做法

```text
把 Hive 全表导入 Nebula
把对象属性只存 Nebula
Action 写回直接写 Nebula
前端直连 Nebula
图库和 SQL 各维护一套对象身份
```

## 10. 分阶段落地

### 阶段 0：PoC

```text
单机 Nebula
小样本对象/边
验证多跳和路径
```

已完成。

### 阶段 1：只读图

```text
节点/边同步任务
traverse_objects
find_paths
member 权限
降级
```

### 阶段 2：Agent 接入

```text
ontology_tool 增加 graph action
提示词路由
结果与属性回源
```

### 阶段 3：生产评估

```text
数据量/延迟/同步失败率
Nebula 集群/备份
是否需要商业支持
是否需要换 HugeGraph/TuGraph
```

## 11. 当前实现状态

已落地代码：

```text
services/ontology/graph/contracts.py
services/ontology/graph/service.py
services/ontology/graph/sync.py
services/ontology/graph/README.md
```

已接入 `ontology_tool`：

```text
traverse_objects
find_graph_paths
```

已接入手动同步：

```text
POST /admin/meta/ontology/graph/sync
```

实测（项目对象投影，批量全量）：

```text
6 个对象类型
21,107 个节点
29,792 条边
0 个错误
```

默认关闭：

```text
CHAT_DATA_NEBULA_ENABLED=false
```

打开后：

```text
depth <= 3
limit <= 200
必须且只能指定一个 member_datasource_id
```

图查询返回的只有对象身份/标题/关系；属性仍回源 `get_object`。

同步实现：

```text
对象类型一条 SELECT 分页
关系一条 SELECT 分页
Nebula 按 200 条分块写
```

## 12. 当前结论

```text
NebulaGraph 已接入为只读图读模型
接入点不是“本体主存储”
```

下一步才是同步任务与生产化；只有真实业务出现深路径/图算法需求，才扩大图的使用范围。
