# TODO：Ossie 导入链路治理（核心链路：稳定 / 完整 / 安全）

> 来源：2026-09-19 全链路 review（导出物化 → YAML → 预览/导入端点 → 前端弹窗 → agent 自检）。
> 结论总评：安全过关；稳定机制设计正确但有 1 处模板漂移；完整性短板 = 数据字典闭环 + 测试缺口。

---

## P1（必修，核心链路不许带病）

- [ ] **1. 补 `semantic_config` 建表模板缺失的同名唯一索引**
  - 位置：`server/utils/schema.py:302-328`
  - 问题：模板 DDL 只有 `uniq_semantic_config_id` + 普通索引；代码声称「实际生效的唯一索引是两列」，真实索引 `(group_id, semantic_config_name, delete_flag)` 只存在于手工迁移 SQL（原 `next_version.md:61`）。
  - 后果：新环境按仓库模板建表即无 DB 兜底；导入冲突检测在事务外取快照（`routers/semantic_config.py:787`）、事务内才落库（`:801`），TOCTOU 窗口内并发同名导入会重复创建。
  - 动作：把唯一索引加回 schema.py 模板（含存量环境的迁移说明）。性价比最高。

- [ ] **2. 补链路级测试（当前为零）**
  - `prepare_ossie_import_models` / `_build_existing_config_index` / `_apply_physical_validation` / conflict_locked 标注：tests 内零引用（`server/apps/chat_data_app/tests/`）。
  - `ossie-import` + `ossie-import/preview` 两个端点无 API 级测试。
  - 覆盖点：同名冲突四分支（skip/replace/skip且locked/物理校验拦截）、事务整体回滚、conflict_actions 非法值、数据源不存在、preview 与 import 结果一致性。
  - 现状：importer 38+24、converter 13+12 用例全在纯函数层，链路行为无回归网。

- [ ] **3. 数据字典闭环（功能新增，先拍板冲突策略再动手）**
  - 缺口：
    - 导出：`chat_data_ossie_converter.py:395`（指标侧 `:510`）`description = item.get("description") or item.get("metadata_supplement")` —— 维度已有 description 时字典派生补充说明整体丢弃；
    - 导出：`allowed_values`/`mapping` 无导出通道（`_dimension_extension_data:134` 只写 `logical_type=enum`）；
    - 导入：`chat_data_ossie_importer.py:302-339` `_convert_field` 不读 allowed_values/mapping，也不回写 `datasource_column_dict`；
    - agent 侧：`agent/skills/data-analyzing/**` 与 `agent/prompts/chat_data.py` 无「字典」概念。
  - 字典表：`datasource_column_dict`（`server/utils/models.py:746`，DDL `server/utils/schema.py:299`），内容仅 enum_values/synonyms/storage_format/unit；唯一写入口 `routers/datasource_admin.py:397-427` → `daos/datasource_dao.py:932`。
  - 方案要点：① 导出侧把四项字典信息写入 field 的 CHAT_DATA custom_extensions（不能压成 description 文本）；② 导入侧 `_convert_field` 读取并落库字典；③ 冲突策略三选一：覆盖 / 合并 / 仅填空；④ 字典直写是否也走「草稿+人工确认」语义（与 `_persist_one_ossie_model` 强制 `enabled=False` 对齐）待决策。

## P2（随手修或排期）

- [ ] 4. parse 错误回传原始 yaml 异常文本（`chat_data_ossie_importer.py:134`）——清洗后再返回，避免泄露源文件片段。
- [ ] 5. 无单文档模型数上限：2MB 可塞数千小模型，preview 逐模型跑物理校验+质量检查（`routers/semantic_config.py:672-690`）是慢查询面。加一个合理上限（如 200）。
- [ ] 6. preview→import 之间被并发新建同名时，前端 `conflict_actions` 缺名 → 后端缺省 skip（`routers/semantic_config.py:905`），结果变「忽略」。可接受，但应在汇总提示中显式说明该情形。
- [ ] 7. 治理闭环断点：agent 产物在会话工作区，导入弹窗只收本地文件（`OssieImportDialog.vue:174-181`）——考虑支持从工作区直接选取。
- [ ] 8. 前端不预检 2MB 文件大小，超限到后端 pydantic 才被拒（`schema.py:615`）——前端选择文件时同步校验。

## 做对的设计（保持不动）

- extensions 锚点往返无损：派生/复合指标经 `metric_kind`/`base_metric`/`formula` 还原（`importer.py:9-13`、`:432`）；`one_to_many` 导出反转/导入还原对称（`converter.py:566-570` ↔ `importer.py:582-592`）。
- 单一权威实现：HTTP preview 与 agent `check_yaml` 共用 `prepare_ossie_import_models`（`data_analyzing.py:499`）。
- 安全基线：`yaml.safe_load`（`:132`）；2MB 限额；导入强制 `enabled=False`（`routers/semantic_config.py:894,921`）；conflict_locked 不泄露 config_id；物化文件名清洗防穿越（`semantic_materializer.py:45-65`）。
- 稳定基线：全有或全无事务（`routers/semantic_config.py:800-815`）；前端文件粒度提交+成功即 removeFile+replace 二次确认（`OssieImportDialog.vue:555-571,596-600`）。
