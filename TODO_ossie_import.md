# TODO：Ossie 导入链路治理（核心链路：稳定 / 完整 / 安全）

> 来源：2026-09-19 全链路 review（导出物化 → YAML → 预览/导入端点 → 前端弹窗 → agent 自检）。
> 结论总评：安全过关；稳定机制设计正确但有 1 处模板漂移；完整性短板 = 数据字典闭环 + 测试缺口。

---

## P1（必修，核心链路不许带病）

- [x] **1. 补 `semantic_config` 建表模板缺失的同名唯一索引**（2026-09-19 完成）
  - 已做：`schema.py` 模板加 `UNIQUE KEY uniq_group_id_config_name_delete_flag (group_id, semantic_config_name, delete_flag)`，与 `next_version.md:61` 手工迁移同名同列序。
  - ~~遗留核对：`semantic_config_dao.py:20` 注释称两列~~（2026-09-19 关闭：生产 SHOW CREATE TABLE 确认为三列 `uniq_group_id_config_name_delete_flag`，无旧索引需 DROP；dao 注释已更新，软删除改名逻辑保留——同名反复删除会积累多行 `(group_id, name, 1)` 撞三列键，改名仍必需）。

- [x] **2. 补链路级测试**（2026-09-19 完成，原「零测试」判断系误报，已收回）
  - 误报原因：上轮 review 用 Bash grep `\|` 交替检索（已知会静默失效），漏掉了 09-18 commit `5b514c7` 就已存在的 14 个端点级用例（直接调用 `preview_admin_ossie_semantic_import` / `import_admin_ossie_semantic_configs`，覆盖：preview 成功/物理校验失败、import 强制 disabled、replace/skip/非法 conflict_action/locked 强制跳过/事务回滚/请求内重名/不泄露 locked 元信息）。
  - 本次补充：`test_chat_data_ossie_importer.py` 追加 6 个直接契约用例——`prepare_ossie_import_models`（重名返回错误+空列表、公共冲突 existing_config 且不 locked、他人私有 locked 且不泄露）+ `_build_existing_config_index`（同名首见优先、缺名/缺 id 跳过、admin 口径私有全进 locked）。文件共 44 用例全绿。

- [x] **3. 数据字典闭环（功能新增）——已完成（2026-09-19）。拍板：冲突策略=仅填空；字典直写不走草稿，导入时直接落库**
  - 拍板依据：导入本身是人工触发的 preview→confirm 流程（preview 的⑤数据字典 checklist 行即确认面），仅填空又非破坏性（已有值一律不覆盖），草稿+人工确认属于重复确认。
  - DDL 结论：**本闭环不需要任何 ALTER，也无需登记建表语句**——字典四项全在 `datasource_column_dict.dict_json` 内，生产已建过该表（用户 2026-09-19 确认，此前误加的建表块已从 `next_version.md` 撤下）。另：`semantic_config` 三列唯一索引生产已执行（2026-09-19 SHOW CREATE TABLE 核对），:12 遗留核对随之关闭——生产为三列形态，无旧两列索引需 DROP。
  - 已实现（导出）：`SemanticConfigService.annotate_export_column_dicts` 把命中字典挂到配置副本 `_column_dict`（键口径与运行时合并单源 `_column_dict_key`）；materializer 渲染前标注（进缓存键，字典变更自动失效）；converter `_column_dict_extension_data` 把四项写入维度/原子指标 CHAT_DATA 扩展（空值不写，不压进 description）。
  - 已实现（导入）：importer `_column_dict_from_extension` 提取 + `OssieSemanticModelImport.column_dicts` 携带（派生/复合指标无物理列不提取，同字段取首见）；`plan_column_dict_fills` 对照现有字典算填充计划；preview 端点返回 `column_dict_fill_plan` + checklist⑤数据字典行；import 端点在同一事务内对 created/replaced 模型执行 `DataSourceDao.fill_column_dict_async`（仅填空，失败整批回滚；skipped 模型不写字典），payload 带 `column_dict_filled` 明细；agent check_yaml 工具路径 checklist 显示 skip 行（未评估）。
  - 已实现（service 单源）：`extract_metric_source_column` 下放 importer 模块，service `_extract_metric_source_column` 委托之（导出标注与导入回填定位同一物理列）。
  - 测试：新增 15 个用例（round-trip、提取归一去重、仅填空计划、checklist 三态、preview 计划、import 填充/跳过不写/失败回滚、DAO 仅填空×4、materializer 标注）；全量 unit 3126 全绿；vue-tsc 0 error。
  - 缺口（已全部闭合）：
    - 导出：`chat_data_ossie_converter.py:395`（指标侧 `:510`）`description = item.get("description") or item.get("metadata_supplement")` —— 维度已有 description 时字典派生补充说明整体丢弃；
    - 导出：`allowed_values`/`mapping` 无导出通道（`_dimension_extension_data:134` 只写 `logical_type=enum`）；
    - 导入：`chat_data_ossie_importer.py:302-339` `_convert_field` 不读 allowed_values/mapping，也不回写 `datasource_column_dict`；
    - agent 侧：`agent/skills/data-analyzing/**` 与 `agent/prompts/chat_data.py` 无「字典」概念。
  - 字典表：`datasource_column_dict`（`server/utils/models.py:746`，DDL `server/utils/schema.py:299`），内容仅 enum_values/synonyms/storage_format/unit；唯一写入口 `routers/datasource_admin.py:397-427` → `daos/datasource_dao.py:932`。
  - 方案要点：① 导出侧把四项字典信息写入 field 的 CHAT_DATA custom_extensions（不能压成 description 文本）；② 导入侧 `_convert_field` 读取并落库字典；③ 冲突策略三选一：覆盖 / 合并 / 仅填空；④ 字典直写是否也走「草稿+人工确认」语义（与 `_persist_one_ossie_model` 强制 `enabled=False` 对齐）待决策。

## P2（随手修或排期）

- [x] 4. parse 错误回传原始 yaml 异常文本（`chat_data_ossie_importer.py:134`）——清洗后再返回，避免泄露源文件片段。（2026-09-22 完成：`parse_ossie_document` 捕获 `yaml.YAMLError` 后改用 `problem_mark` 只输出「第 X 行第 Y 列」，不再回显源行片段；无 mark 时退回通用文案。三端（HTTP preview / import / agent check_yaml）共用该实现自动生效，测试断言同步更新为具体行列号）
- [ ] 5. 无单文档模型数上限：2MB 可塞数千小模型，preview 逐模型跑物理校验+质量检查（`routers/semantic_config.py:672-690`）是慢查询面。加一个合理上限（如 200）。
- [ ] 6. preview→import 之间被并发新建同名时，前端 `conflict_actions` 缺名 → 后端缺省 skip（`routers/semantic_config.py:905`），结果变「忽略」。可接受，但应在汇总提示中显式说明该情形。
- [ ] 7. 治理闭环断点：agent 产物在会话工作区，导入弹窗只收本地文件（`OssieImportDialog.vue:174-181`）——考虑支持从工作区直接选取。
- [ ] 8. 前端不预检 2MB 文件大小，超限到后端 pydantic 才被拒（`schema.py:615`）——前端选择文件时同步校验。

## 做对的设计（保持不动）

- extensions 锚点往返无损：派生/复合指标经 `metric_kind`/`base_metric`/`formula` 还原（`importer.py:9-13`、`:432`）；`one_to_many` 导出反转/导入还原对称（`converter.py:566-570` ↔ `importer.py:582-592`）。
- 单一权威实现：HTTP preview 与 agent `check_yaml` 共用 `prepare_ossie_import_models`（`data_analyzing.py:499`）。
- 安全基线：`yaml.safe_load`（`:132`）；2MB 限额；导入强制 `enabled=False`（`routers/semantic_config.py:894,921`）；conflict_locked 不泄露 config_id；物化文件名清洗防穿越（`semantic_materializer.py:45-65`）。
- 稳定基线：全有或全无事务（`routers/semantic_config.py:800-815`）；前端文件粒度提交+成功即 removeFile+replace 二次确认（`OssieImportDialog.vue:555-571,596-600`）。
