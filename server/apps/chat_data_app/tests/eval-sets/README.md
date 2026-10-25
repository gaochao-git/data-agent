# eval-sets

评测集目录：存放供测试人员 / 评测智能体使用的问答对，与代码单测（`tests/unit`、`tests/api`）分开。

## 文件

| 文件 | 覆盖范围 |
|---|---|
| `个体分析评测集.md` | 单个客户/订单的查询、关系跳转、明细统计、机构范围、防编造 |
| `全局分析评测集.md` | 按时间/机构/支付状态的聚合与对比（2025 年各机构订单量等） |

约定：

- 一个文件一个评测集，`eval_set_id` 取文件名；
- 个体分析 = 看某个对象/某笔单据；全局分析 = 看整体分布/汇总/对比；
- 每个用例按「测试目标 → 问题 → 答案」维护；
- 问题与答案只用业务语言（机构名、订单号、金额），不出现 `ORG_CODE`、member id 等内部代码；
- 答案以 Hive 实测为准，数据变化后先重新校验再跑。

## 运行环境

- 浏览器测试
- URL：http://localhost:8089/chatdata?parentComponentName=执法检查
- 数据源组：hive-localhost（财付通等 6 个机构）
- 模型：deepseek/deepseek-v4-flash
- 数据时点：2026-10-25 20:24 CST（已按 Hive 实测校验）
- 每题新建会话、只读、单题最多 180 秒
- 并发会话：每次开启 2 个会话并行测试

## 数据环境与校验

跑评测前先确认环境；数据变了或 Hive 不可用，评测集答案即失效，不能直接拿来判定。

**Hive 环境（本地 Docker）**

- 运行方式：本机 Docker 容器 `hive`，镜像 `apache/hive:4.0.0`
- 端口：`10000`（HiveServer2）、`9083`（Metastore）、`10002`
- 地址：`localhost:10000`（Hive 直连，按机构分库）
- 用户名：`hive`
- 密码：无（容器未开启认证，连接参数不需要 password；如后续启用，改 `CHAT_DATA_HIVE_USER` / `CHAT_DATA_HIVE_PASSWORD`）
- 库：`org_code1` 财付通、`org_code2` 支付宝、`org_code3` 平安付、`org_code4` 拉卡拉、`org_code5` 银商务、`org_code6` 货拉拉
- 关键表：`users`、`orders`、`order_items`、`shipments`、`products`、`categories`
- 自查容器：`docker ps | grep hive`（确认运行中且 `10000` 端口已映射）

**如何连接 Hive（Python）**

Python 环境用 conda 的 `fxcjg`，不要用系统 Python（可能没装 pyhive）：

```text
/Users/gaochao/work/miniconda3/envs/fxcjg/bin/python   # Python 3.12.12，pyhive 0.7.0
```

连接与查询示例：

```bash
cd /Users/gaochao/work/git_repo/data-agent/server
/Users/gaochao/work/miniconda3/envs/fxcjg/bin/python - <<'PY'
from pyhive import hive

conn = hive.Connection(
    host="localhost",
    port=10000,
    database="org_code1",   # 换机构改这里：org_code2 支付宝、org_code3 平安付……
    username="hive",
    # 开发库无认证，不需要 password
)
cur = conn.cursor()
cur.execute("SELECT user_id, username FROM users WHERE username='杜佑'")
print(cur.fetchall())   # [(1000001, '杜佑')]
conn.close()
PY
```

容器没起时先执行 `docker start hive`；连接报错优先确认 `docker ps` 里 `10000` 端口是否映射。

**校验 SQL（org_code1）**

```sql
-- 1. 杜佑只在财付通；org_code2~6 应返回 0
SELECT user_id, username, email FROM users WHERE username='杜佑';
-- 预期：1000001 / 杜佑 / u1000001@ftpay.example.com

-- 2. 杜佑的订单
SELECT order_id, amount, status FROM orders WHERE user_id=1000001 ORDER BY order_id;
-- 预期：ORG1-ORD001906 1502.46 refunded；ORG1-ORD004041 537.51 delivered

-- 3. 明细条数
SELECT order_id, COUNT(*) FROM order_items
WHERE order_id IN ('ORG1-ORD001906','ORG1-ORD004041') GROUP BY order_id;
-- 预期：1906=3，4041=2

-- 4. 发货单与物流
SELECT shipment_id, order_id, carrier, status, tracking_no FROM shipments
WHERE order_id IN ('ORG1-ORD001906','ORG1-ORD004041');
-- 预期：各 1 张，中通；单号 ORG1-TRK0000001821 / ORG1-TRK0000003851
```

当前实测（2026-10-25 20:24 CST）：`users=1200`、`orders=5000`、`order_items=9925`、`shipments=4732`；订单状态分布：cancelled 254、delivered 4222、paid 217、pending 60、refunded 135、shipped 112。

**异常处理**

- 任一校验 SQL 结果与上面不一致 → 评测集答案已过期，先更新答案或恢复数据，不要直接跑；
- Hive 不可用、或后端 `5001` 不可用 → 本次评测标记 `BLOCKED`，不能用旧结果下结论；
- `bak24_*` 是另一批旧数据（`bak24_users` 仅 71 行且没有杜佑），**不能**作为评测集基准；
- 智能体自查：在当前会话数据源组 `hive-localhost` 下用本体/语义工具核对象（`find_objects`、`get_object`、`aggregate_objects`），或按上面 SQL 直连 Hive 核对。
