# 举报件专家经验：上传与使用说明

## 上传入口与目录结构
- 接口：`POST /api/complaint-analysis-uploaded`（仅支持 `xlsx/xls`）
- 存储根：`DATA_ROOT/complaints/expert_experience/complaint-analysis-uploaded`
- 子目录：`YYYYMMDD_HHMMSS_<6位随机>`，包含：
  - `original.xlsx|original.xls`：原始上传文件
  - `parsed.json`：解析后的结构化数据（按一级分类分组）
  - `meta.json`：元数据（`id/created_at/file_name/sheet/columns_detected/rows_count/overview_count`）

## Excel 解析与字段
- 表头识别：前 10 行扫描匹配期望列名（含中文列名与别名）
- 关键列映射：
  - `一级分类` → `category`
  - `一级分类说明` → `first_category_description`
  - `名词辨析` → `glossary`
  - `二级分类` → `overview`
  - `二级分类说明` → `second_category_description`
  - `核实事项` → `decomposition`
  - `是否属于支付部门核实范围` → `in_scope`
  - `处理建议` → `suggestions`
  - `法律法规` → `law_list`
  - `核实要点` → `verification_points`
  - `调阅清单` → `checklist`
  - `注意事项` → `notes`

## 服务读取与缓存
- 读取最新数据：`load_latest_expert_experience_json` 从最新子目录的 `parsed.json` 返回 JSON 字符串
- TTL 缓存：`get_expert_experience_cached(ttl_seconds=60)` 提供 60 秒多线程安全缓存（异常返回空字符串）

## 在当前流程中的使用
- 历史与追问视图：
  - 基于最新事项分析结果，按事项分类匹配专家经验，生成“举报事项与诉求及相关专家分析”视图，用于历史详情与追问上下文。
  - 命中且 `in_scope == "是"`：展示“法律依据、核实要点、调阅清单、核实事项、注意事项、处理建议”，并在“参考分类”中呈现“一级分类定义、名词解释、二级分类说明”。
  - 不在范围：呈现“二级分类说明、一级分类定义、名词解释”与“处理建议”。

## 字段变更（不兼容旧表）
- 新增列：
  - `一级分类说明` → `first_category_description`
  - `名词辨析` → `glossary`
- 重命名列：
  - `定义` 改为 `二级分类说明` → `second_category_description`
- 视图封装：
  - “参考分类”中新增“一级分类定义”“名词解释”，并将“二级分类定义”来源改为 `second_category_description`。

## 相关代码入口
- 上传与解析：`server/apps/complaint_app/services/expert_upload_service.py`
- 数据读取与匹配：`server/apps/complaint_app/services/history_service.py`
- 视图构造与聊天上下文：`server/apps/complaint_app/services/chats/chat.py`
