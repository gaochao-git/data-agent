# 语义查询方言参考

本文件只处理 Hive 和 MySQL 的方言差异。当前使用的方言以本轮系统数据上下文中的 `source_type` 为准；上下文缺失时遵循系统默认方言。不要根据历史消息或模型常识猜测方言。

## DSL 时间分组

DSL 时间分组始终使用：

```json
{"name":"已确认的时间维度","time_grain":"month"}
```

不要在 DSL 中写 `DATE_FORMAT`、`%Y-%m`、`yyyy-MM` 或其他 SQL 格式串。具体格式由 DSL 编译器根据数据源方言生成。

## Hive

- 按月：`DATE_FORMAT(time_col, 'yyyy-MM')`
- 按日：`DATE_FORMAT(time_col, 'yyyy-MM-dd')`
- 按小时：`DATE_FORMAT(time_col, 'yyyy-MM-dd HH:00')`
- 不要使用 MySQL 风格的 `%Y-%m`、`%Y-%m-%d`、`%H`。
- 如果结果出现 `%2024-%0`、`%2025-%0` 等形式，通常是方言格式串错误。修正 DSL 或方言上下文后重新走 DSL 编译，不要手工改写生成 SQL。

## MySQL

- 按月：`DATE_FORMAT(time_col, '%Y-%m')`
- 按日：`DATE_FORMAT(time_col, '%Y-%m-%d')`
- 按小时：`DATE_FORMAT(time_col, '%Y-%m-%d %H:00')`
- 不要使用 Hive 风格的 `yyyy-MM`、`yyyy-MM-dd`、`HH`。

## 原生 SQL 回退

- 原生 SQL 必须符合当前 `source_type` 的方言。
- 如果工具或服务端已经生成 SQL，不要手工替换其中的日期格式或函数。
- 看到方言上下文与生成结果明显不一致时，停止当前分支并说明方言上下文异常，不能把 Hive SQL 和 MySQL SQL 互相手工改写。
