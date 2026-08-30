# ChatData Evaluate

`evaluate/` 用于管理智能问数的离线评估与压测，不放入常规 `pytest` 单元测试链路。

## 目录边界

- `accuracy/`：准确率评估，覆盖 NL -> DSL -> SQL -> 执行结果，按指标集维护 golden cases。

## 与 tests/ 的区别

- `tests/`：快、确定性强，适合 CI，尽量不依赖真实 LLM / Hive / MySQL。
- `evaluate/`：允许慢，允许依赖真实服务和数据源，用于评估产品效果和性能。
