# Meta Services

本目录承载公司级元数据（跨数据源、跨环境共享的配置型数据）的业务服务，当前仅含全局枚举。

## 全局枚举（global_enum_service.py）

- 全局枚举表 `chat_data_meta_global_enum` 是全系统枚举值域唯一权威；唯一键 `(business_domain, enum_name)`，不同业务域同名枚举互相隔离。
- 数据字典列通过 `dict_json.enum_ref` 引用（二选一：`enum_ref` 非空时本地 `enum_values` 必须为空）。
- **所有枚举解析消费方必须走 `resolve_column_dicts_async` 单一公共入口**，不得自行实现 enum_ref 拆分或二选一判断（`_split_enum_ref` 统一从 DAO 导入）。
- 写入校验统一走 `validate_column_dict_for_write_async`（datasource manager 写字典前调用）；删除保护（引用中拒绝删除）在 `delete_enum_async` 内实现。
- 引用计数与引用方查询遵循「禁止数据库原生 JSON 语法」：LIKE 字符串粗筛 + 应用层 JSON 精确解析两步法（DAO 内实现）。
- 物化缓存失效不靠版本盐：resolve 发生在字典进入物化之前，解析后的枚举值参与缓存盐哈希，枚举内容一变自动物化自然感知。

HTTP 入口见 `routers/meta_admin.py`（Admin 鉴权）；DAO 见 `daos/meta_global_enum_dao.py`。

新增其他元数据类型（单位、存储格式等）时在本目录扩展 service，并在 `MetaManagementPanel.vue` 的类型导航中登记。
