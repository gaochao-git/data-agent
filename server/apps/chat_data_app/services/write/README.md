# write

写路径适配器。查询走 `services/query`，写入不混入查询层。

- `hive.py`：Hive direct 唯一写入入口；同步 pyhive 调用经线程池执行。
- 业务服务负责生成已校验的 DDL/DML，适配器只负责连接与执行。
- 新增存储类型时扩展适配器，不修改业务服务。
