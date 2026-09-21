# SQL Run

本目录负责 SQL 解析、安全校验、执行器、查询结果、缓存、并发限制、异步任务、查询历史和查询收藏。

语义 DSL 的编译由 `services/semantic/run/` 编排并调用 `services/semantic/core/`，本目录只负责实际 SQL 运行生命周期。

## 执行前安全校验（sql_guard.py，唯一权威入口）

所有执行路径（agent 工具、SQL 编辑器任务、执行器边界复检）进入数据库前的校验统一走 `sql_guard.py`：

- `run_sql_guard(sql, source_type, max_rows, *, defer_syntax_error)`：规则管线，按序执行六条规则（单语句 / 命令类型白名单 / INTO 副作用 / 高危函数 / 锁定读 / LIMIT 显式超限），返回 `SqlGuardViolation`（含 rule、message、reason_code、internal、syntax_error）。
- `check_effective_limit_gate(...)`：终检门，仅在 `build_effective_sql` 出口使用——进入执行的 SELECT 产物必须带合法最外层 LIMIT；缺失属系统内部缺陷信号（`internal=True`）。
- 新增拦截规则 = 新增一个 `check_*` 纯函数并挂入管线，不要打开既有函数往里塞。
- 拒绝文案是报警细分码推断（`utils.alarm._infer_sql_block_reason` / `_SQL_BLOCK_REASON_MAP`）的契约，修改措辞必须同步 `utils/alarm.py`。

防线分层：提交前（建任务前）仅多语句当场拒绝 → `build_effective_sql` 追加 LIMIT 后终检 → 执行器边界复检（命令类型/副作用/高危函数/LIMIT 在此拦截）。语法错误与内容类违规统一延迟到执行期，由每个执行对象生成独立失败结果并进结果面板展示（SQL 内容错误不弹全局通知）；SQL 不会因延迟在提交阶段触达数据库——边界复检是全规则的。
