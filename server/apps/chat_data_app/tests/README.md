# ChatData 测试说明

当前测试目录只保留仍在维护的测试资产：

```text
tests/
├── README.md
├── generate_openapi.py  # 从真实 FastAPI app 导出接口契约
├── unit/   # 单元测试：函数、编译器、服务逻辑，不依赖真实 HTTP 服务
└── api/    # API 契约测试：走 FastAPI 路由，外部依赖 mock，开发环境手动执行
```

## 默认测试

默认 `pytest` 只收集 `unit/`，用于本地提交和后续 CI/CD：

```bash
cd server
pytest
```

覆盖率统计：

```bash
cd server
python -m coverage erase
python -m coverage run --source=apps/chat_data_app --branch -m pytest
python -m coverage report -m --sort=miss
```

## API 契约测试

`api/` 目录用于开发阶段手动验证接口契约，不纳入默认 `pytest` 收集范围。它会走真实 FastAPI 路由、请求校验、认证依赖和统一响应，数据库、LLM、Hive、数据源管理器等外部边界通过 mock 隔离。

```bash
cd server
pytest apps/chat_data_app/tests/api -q
```

## OpenAPI 接口契约快照

`api/openapi.json` 是从真实 `main.app` 导出的当前接口契约，用于查看接口规模、审查接口变更，并作为后续 API 测试覆盖清单的权威输入。新增、删除或调整接口后必须重新生成并提交快照：

```bash
cd server
python apps/chat_data_app/tests/generate_openapi.py
```

在 CI 中重新生成后执行 `git diff --exit-code apps/chat_data_app/tests/api/openapi.json`，可防止代码接口已变更但契约快照未更新。

## 准确率评测

旧的 `tests/evaluator.py`、`tests/integration/test_runner.py`、`nl2sql_tests/`、`nl2dsl_tests/`、`dsl2sql_tests/` 和历史 `reports/` 已删除。

现行准确率评测走产品页面和后端评测接口：

- 接口入口：`apps/chat_data_app/routers/evaluation.py`
- 业务服务：`apps/chat_data_app/services/evaluation_service.py`
- 执行器：`apps/chat_data_app/evaluate/accuracy/`

新增评测能力时，应优先补充页面/API评测链路和对应单元测试，不再恢复旧的离线样本 runner。
