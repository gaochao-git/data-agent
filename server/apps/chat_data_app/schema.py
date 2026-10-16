"""
ChatData API 数据模型定义
"""

import json
from datetime import datetime
from typing import Any, ForwardRef, Literal, cast

from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from utils.builtin_skills import DATA_ANALYZING_SKILL

from .chart_types import ChartType
from .services.runtime_config import MAX_QUERY_TIMEOUT_SECONDS
from .services.semantic.core.input_validator import FILTER_OPERATORS


class ApiResponse(BaseModel):
    """ChatData JSON 接口统一响应包络。"""

    status: Literal["success", "error"]
    msg: str
    code: int
    data: Any | None = None


QueryExecutorType = Literal["direct", "http"]
MAX_RECOMMENDED_QUESTION_COUNT = 50
MAX_RECOMMENDED_QUESTION_LENGTH = 50


def _normalize_json_object(value: Any, field_name: str) -> Any:
    """兼容部分模型将嵌套工具对象序列化为 JSON 字符串。"""
    current = value
    for _ in range(2):
        if not isinstance(current, str):
            break
        try:
            current = json.loads(current.strip())
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} 必须是对象或有效的 JSON 对象字符串") from exc
    if isinstance(current, str) or not isinstance(current, dict):
        raise ValueError(f"{field_name} 必须是对象或有效的 JSON 对象字符串")
    return current


def _normalize_recommended_questions(value: Any) -> list[str]:
    if value is None:
        return []
    raw_items = value.splitlines() if isinstance(value, str) else value
    if not isinstance(raw_items, list):
        raise ValueError("recommended_questions 必须是数组")

    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(raw_items):
        question = str(item or "").strip()
        if not question:
            continue
        if len(question) > MAX_RECOMMENDED_QUESTION_LENGTH:
            raise ValueError(f"推荐问题第 {index + 1} 行不能超过 {MAX_RECOMMENDED_QUESTION_LENGTH} 个字")
        if question in seen:
            continue
        seen.add(question)
        result.append(question)
        if len(result) > MAX_RECOMMENDED_QUESTION_COUNT:
            raise ValueError(f"推荐问题不能超过 {MAX_RECOMMENDED_QUESTION_COUNT} 条")
    return result


class ExecuteSqlRequest(BaseModel):
    """SQL 执行运行时请求（供异步任务、智能体和内部复用）"""

    sql: str = Field(..., min_length=1, description="要执行的 SQL 语句")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: list[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: str | None = Field(default=None, description="当前用户ID")
    conversation_id: str | None = Field(default=None, description="来源会话ID")
    query_text: str | None = Field(default=None, description="原始用户问题或查询说明")
    semantic_config_id: str | None = Field(default=None, description="语义配置ID")
    limit: int | None = Field(default=None, description="结果行数限制；为空时由服务端按运行时配置解析默认值")
    timeout: int | None = Field(
        default=None,
        ge=1,
        le=MAX_QUERY_TIMEOUT_SECONDS,
        description="查询超时时间（秒），为空时按数据源类型取全局默认值，上限 3600",
    )
    query_type: str | None = Field(default="sql", description="内部查询类型: sql | builder | agent")
    builder_config: dict | None = Field(default=None, description="辅助查询模式的配置")
    semantic_builder_config: dict | None = Field(default=None, description="语义可视化查询 DSL；服务端据此重新编译 SQL")
    skip_query_cache: bool = Field(default=False, description="是否跳过 SQL 查询结果缓存")


class QueryTaskCreateRequest(BaseModel):
    """异步查询任务创建请求"""

    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: list[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: str | None = Field(default=None, description="当前用户ID")
    operator_name: str | None = Field(default=None, description="当前操作人名称")
    conversation_id: str | None = Field(default=None, description="来源会话ID")
    query_text: str | None = Field(default=None, description="原始用户问题或查询说明")
    semantic_config_id: str | None = Field(default=None, description="语义配置ID")
    sql: list[str] = Field(default_factory=list, description="要执行的 SQL 语句列表；builder 查询由服务端根据 DSL 编译")
    limit: int | None = Field(default=None, description="结果行数限制；为空时由服务端按运行时配置解析默认值")
    timeout: int | None = Field(
        default=None,
        ge=1,
        le=MAX_QUERY_TIMEOUT_SECONDS,
        description="查询超时时间（秒），为空时按数据源类型取全局默认值，上限 3600",
    )
    query_type: Literal["sql", "builder"] = Field(default="sql", description="查询类型: sql | builder")
    builder_config: dict | None = Field(default=None, description="辅助查询模式的配置")
    semantic_builder_config: dict | None = Field(default=None, description="语义可视化查询 DSL；服务端据此重新编译 SQL")
    skip_query_cache: bool = Field(default=False, description="是否跳过 SQL 查询结果缓存")

    @model_validator(mode="after")
    def validate_sql_list(self) -> "QueryTaskCreateRequest":
        self.sql = [str(item or "").strip() for item in self.sql if str(item or "").strip()]
        query_type = str(self.query_type or "sql").strip().lower()
        if query_type == "builder":
            if len(self.sql) > 1:
                raise ValueError("SQL 批次不支持语义可视化查询")
            return self
        if not self.sql:
            raise ValueError("SQL 至少包含一条有效语句")
        if len(self.sql) > 1 and self.semantic_builder_config:
            raise ValueError("SQL 批次不支持语义可视化查询")
        return self


class InternalQueryTaskCreateRequest(QueryTaskCreateRequest):
    """服务端内部查询任务请求，额外允许标记智能体来源。"""

    # 有意放宽：内部请求额外允许 agent 来源（基类对外仅暴露 sql/builder）
    query_type: Literal["sql", "builder", "agent"] = Field(default="sql", description="内部查询类型")  # type: ignore[assignment]


class CompileSemanticBuilderSqlRequest(BaseModel):
    """编译语义可视化 DSL 请求（仅生成 SQL，不执行）。"""

    datasource_id: str = Field(..., description="数据源ID")
    semantic_config_id: str = Field(..., description="语义配置ID")
    semantic_builder_config: dict = Field(..., description="语义可视化 DSL；不包含 mainTable/joins")


class QueryFavoriteCreateRequest(BaseModel):
    """创建查询收藏请求"""

    datasource_id: str = Field(..., description="数据源ID")
    query_type: Literal["conversation", "builder", "sql"] = Field(..., description="收藏类型")
    favorite_title: str = Field(..., min_length=1, max_length=255, description="收藏标题")
    query_text: str = Field(..., min_length=1, description="原始查询内容（问题或SQL）")
    builder_config: dict | None = Field(default=None, description="可视化查询配置（仅 builder 类型）")
    semantic_config_id: str | None = Field(default=None, description="语义配置ID")


class QueryFavoriteUpdateRequest(BaseModel):
    """更新查询收藏请求"""

    favorite_title: str | None = Field(default=None, min_length=1, max_length=255, description="收藏标题")
    query_text: str | None = Field(default=None, min_length=1, description="原始查询内容（问题或SQL）")
    builder_config: dict | None = Field(default=None, description="可视化查询配置（仅 builder 类型）")
    semantic_config_id: str | None = Field(default=None, description="语义配置ID")


class AgentScheduleCreateRequest(BaseModel):
    """创建智能体定时任务请求"""

    title: str = Field(..., min_length=1, max_length=100, description="任务标题")
    prompt: str = Field(..., min_length=1, max_length=2000, description="任务指令（提示词）")
    conversation_id: str | None = Field(
        default=None, max_length=64, description="绑定的会话ID；conversation_mode=fixed 时必填"
    )
    conversation_mode: Literal["fixed", "new_each_run"] = Field(
        default="fixed", description="会话绑定方式：fixed-指定会话，new_each_run-每次执行新建会话"
    )
    schedule_type: Literal["cron", "once"] = Field(default="cron", description="调度类型：cron-周期，once-单次")
    cron_expr: str | None = Field(
        default=None, max_length=64, description="五字段 cron 表达式（分 时 日 月 周），周期任务必填"
    )
    plan_time: datetime | None = Field(default=None, description="单次任务的执行时间，必须晚于当前时间")
    effective_end_time: datetime | None = Field(default=None, description="有效期结束时间，空表示长期有效")
    datasource_id: str | None = Field(
        default=None, max_length=128, description="执行数据源ID（创建时的便捷字段，等价于 options.datasource_id）"
    )
    options: dict[str, Any] | None = Field(
        default=None, description="运行选项（与智能问数 options 口径一致）；值为 null 的键视为未设置"
    )


class AgentScheduleUpdateRequest(BaseModel):
    """更新智能体定时任务请求（未传字段保持不变）"""

    title: str | None = Field(default=None, min_length=1, max_length=100, description="任务标题")
    prompt: str | None = Field(default=None, min_length=1, max_length=2000, description="任务指令（提示词）")
    schedule_type: Literal["cron", "once"] | None = Field(default=None, description="调度类型")
    conversation_mode: Literal["fixed", "new_each_run"] | None = Field(default=None, description="会话绑定方式")
    conversation_id: str | None = Field(
        default=None, max_length=64, description="绑定的会话ID（切换为指定会话模式时必填）"
    )
    cron_expr: str | None = Field(default=None, max_length=64, description="五字段 cron 表达式")
    plan_time: datetime | None = Field(default=None, description="单次任务的执行时间")
    effective_end_time: datetime | None = Field(
        default=None, description="有效期结束时间；显式传 null 表示清除有效期，不传则保持不变"
    )
    enabled_flag: int | None = Field(default=None, ge=0, le=1, description="启用状态：1-启用，0-停用")
    datasource_id: str | None = Field(
        default=None,
        max_length=128,
        description="执行数据源ID（便捷字段，等价于 options.datasource_id；清除请用 options 补丁传 null）",
    )
    options: dict[str, Any] | None = Field(
        default=None, description="运行选项补丁：只提交要改的键，值为 null 表示清除该键，未提交的键保持不变"
    )


class AgentSchedulePauseRequest(BaseModel):
    """后台全局暂停/恢复定时任务请求"""

    paused: bool = Field(..., description="true-暂停所有调度，false-恢复")
    reason: str | None = Field(default=None, max_length=255, description="暂停原因（可选）")


class AgentScheduleEnabledRequest(BaseModel):
    """后台强制启停单个定时任务请求"""

    enabled_flag: int = Field(..., ge=0, le=1, description="启用状态：1-启用，0-停用")


class ChatDataSettingUpsertRequest(BaseModel):
    """后台配置变量覆盖请求（key 限注册表白名单；已软删的 key 自动复用原行恢复）"""

    setting_value: str = Field(..., min_length=1, max_length=2048, description="变量值（字符串存储，按注册类型解析）")


# ============ 表管理相关模型 ============


class TableListResponse(BaseModel):
    """表列表响应"""

    status: Literal["success", "error"] = "success"
    msg: str = ""
    code: int = 2000
    data: dict[str, list[str]] | None = Field(default=None, description="表名列表 {'tables': ['table1', 'table2']}")


class TableInfoRequest(BaseModel):
    """表信息请求"""

    table_name: str = Field(..., description="表名")
    include_ddl: bool = Field(default=False, description="是否包含DDL语句")


class TableInfoResult(BaseModel):
    """表信息结果"""

    table_name: str = Field(..., description="表名")
    description: str = Field(default="", description="表描述")
    columns: list[dict] = Field(default_factory=list, description="列信息列表")
    ddl: str | None = Field(default=None, description="DDL语句")


class TableInfoResponse(BaseModel):
    """表信息响应"""

    status: Literal["success", "error"] = "success"
    msg: str = ""
    code: int = 2000
    data: TableInfoResult | None = None


# ============ 数据库元数据同步模型 ============


class DbConnectionRequest(BaseModel):
    """数据库连接请求（用于获取表列表）"""

    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    query_executor_type: QueryExecutorType = Field(default="direct", description="查询执行方式: direct | http")
    host: str = Field(default="", description="服务器地址")
    port: int | None = Field(default=None, description="端口（Hive默认10000，MySQL默认3306）")
    database: str = Field(..., description="数据库名")
    org_code: str | None = Field(default=None, description="机构编码")
    org_name: str | None = Field(default=None, description="机构名称")

    @model_validator(mode="after")
    def validate_connection_fields(self) -> "DbConnectionRequest":
        self.host = str(self.host or "").strip()
        self.database = str(self.database or "").strip()
        self.org_code = str(self.org_code or "").strip() or None
        self.org_name = str(self.org_name or "").strip() or None
        if self.query_executor_type == "direct":
            if not self.host:
                raise ValueError("host 不能为空")
        elif not self.org_code:
            raise ValueError("HTTP 模式缺少机构编码")
        elif self.host or self.port is not None:
            raise ValueError("HTTP 模式只填写数据库名，不填写 IP 和端口")
        if not self.database:
            raise ValueError("database 不能为空")
        return self


class DatasourceUpdateRequest(BaseModel):
    """数据源更新请求（元信息）"""

    name: str | None = Field(default=None, description="数据源名称")
    description: str | None = Field(default=None, description="数据源描述")


class DatasourceTableGroupItem(BaseModel):
    """数据源表分组项"""

    table_group_name: str = Field(..., min_length=1, max_length=100, description="表分组名称")
    table_names: list[str] = Field(default_factory=list, description="分组内表名列表")


class DatasourceTableGroupsUpdateRequest(BaseModel):
    """数据源表分组更新请求"""

    groups: list[DatasourceTableGroupItem] = Field(default_factory=list, description="表分组列表")


class DatasourceTableBusinessDescUpdateRequest(BaseModel):
    """数据源表业务说明更新请求"""

    business_desc: str = Field(default="", max_length=255, description="表业务说明")


class DatasourceColumnDictEnumItem(BaseModel):
    """数据源字段字典枚举项"""

    value: str = Field(..., min_length=1, max_length=128, description="枚举值")
    label: str = Field(default="", max_length=255, description="枚举含义")


class DatasourceColumnDictPayload(BaseModel):
    """数据源字段字典内容"""

    # 引用全局枚举（chat_data_meta_global_enum 的 enum_name）；非空时 enum_values 必须为空
    enum_ref: str = Field(default="", max_length=128, description="引用的全局枚举名，空表示使用本地枚举")
    enum_values: list[DatasourceColumnDictEnumItem] = Field(default_factory=list, description="枚举值列表")
    storage_format: str = Field(default="", max_length=255, description="数据存储格式")
    unit: str = Field(default="", max_length=100, description="数值单位")


class DatasourceColumnDictUpdateRequest(BaseModel):
    """数据源字段字典更新请求"""

    model_config = {"populate_by_name": True}

    column_dict: DatasourceColumnDictPayload = Field(
        default_factory=DatasourceColumnDictPayload, alias="dict", description="字段字典"
    )


class DatasourceColumnDictGenerateRequest(BaseModel):
    """数据源字段字典生成请求"""

    text: str = Field(..., min_length=1, max_length=10000, description="用于生成字段字典的文本")


class MetaGlobalEnumItem(BaseModel):
    """全局枚举值项"""

    value: str = Field(..., min_length=1, max_length=128, description="枚举值")
    label: str = Field(default="", max_length=255, description="枚举含义")


class MetaGlobalEnumGenerateRequest(BaseModel):
    """全局枚举值 AI 填充请求（复用字段字典生成链路，无表/字段上下文）"""

    text: str = Field(..., min_length=1, max_length=10000, description="用于提取枚举值的文本")


class MetaGlobalEnumUpsertRequest(BaseModel):
    """全局枚举创建/更新请求（enum_name 由路径参数携带，body 只含内容字段）"""

    business_domain: str = Field(
        default="", max_length=128, description="业务域（如支付业务），空为通用域；不同域同名枚举互相隔离"
    )
    enum_desc: str = Field(default="", max_length=512, description="枚举说明")
    enum_items: list[MetaGlobalEnumItem] = Field(..., min_length=1, description="枚举值列表（非空）")
    # 编辑既有枚举时为 True（允许覆盖内容）；新建/存为模板保持 False，同域同名活跃枚举已存在则拒绝
    allow_overwrite: bool = Field(default=False, description="是否允许覆盖已存在的活跃枚举（仅编辑场景为 True）")


class TableIndexItem(BaseModel):
    """数据源表索引项"""

    index_type: Literal["primary", "unique", "secondary"] = Field(..., description="索引类型")
    index_name: str = Field(default="", max_length=32, description="索引名称")
    columns: list[str] = Field(default_factory=list, max_length=5, description="索引列名列表")


class OntologyPropertyItem(BaseModel):
    """本体属性项（属性一等实体，随对象表单整体提交）"""

    property_name: str = Field(..., min_length=1, max_length=128, description="属性API名，对象内唯一")
    display_name: str = Field(default="", max_length=128, description="展示名")
    description: str = Field(default="", max_length=1024, description="属性描述")
    value_type: Literal["String", "Integer", "Decimal", "Boolean", "Date", "DateTime", "enum"] = Field(
        default="String", description="值类型：内建类型或 enum（引用全局枚举）"
    )
    enum_ref: str = Field(
        default="",
        max_length=260,
        description='全局枚举引用，value_type=enum 时必填："enum_name" 或 "domain:enum_name"',
    )
    render_hints: dict[str, bool] = Field(
        default_factory=dict, description="渲染提示: searchable/sortable/low_cardinality"
    )
    is_identifier: bool = Field(default=False, description="标识属性：参与对象主键组合（业务主键，禁自增行号）")
    is_title: bool = Field(default=False, description="标题属性：对象标题键（每对象至多一个）")


class OntologyBindingItem(BaseModel):
    """本体绑定项（语义模型绑定：口径权威在语义层）"""

    semantic_config_id: str = Field(..., min_length=1, max_length=128, description="语义模型配置ID")
    table_alias: str = Field(..., min_length=1, max_length=128, description="语义模型表别名")
    column_mappings: dict[str, str] = Field(default_factory=dict, description="列映射: {属性API名: 物理列名}")


class OntologyObjectUpsertRequest(BaseModel):
    """本体对象创建/编辑请求（object_name 由路径参数携带；状态走独立接口）"""

    display_name: str = Field(..., min_length=1, max_length=128, description="展示名")
    description: str = Field(default="", max_length=1024, description="对象描述")
    business_notes: str = Field(default="", max_length=10000, description="业务说明（建模背景，不入 agent 上下文）")
    domain: str = Field(default="", max_length=128, description="业务域扁平标签")
    properties: list[OntologyPropertyItem] = Field(..., min_length=1, description="属性集（整体提交）")
    bindings: list[OntologyBindingItem] = Field(
        default_factory=list, description="语义模型绑定列表（独立存映射表，随对象提交全量重写）"
    )
    allow_overwrite: bool = Field(default=False, description="编辑既有活跃对象时为 True")
    confirm_destructive: bool = Field(default=False, description="破坏性变更（绑定/主键/标题键变更）显式确认")


class OntologyObjectStatusRequest(BaseModel):
    """本体对象状态迁移请求"""

    status: Literal["draft", "experimental", "certified", "deprecated"] = Field(..., description="目标状态")
    deprecation_reason: str = Field(default="", max_length=512, description="废弃原因，status=deprecated 时必填")
    deprecated_until: str = Field(default="", max_length=19, description="废弃删除期限（YYYY-MM-DD HH:MM:SS），可选")
    successor_object_name: str = Field(default="", max_length=128, description="替代对象API名，可选但必须存在")


class OntologyRelationUpsertRequest(BaseModel):
    """本体关系创建/编辑请求（relation_name 由路径参数携带）"""

    from_object_name: str = Field(..., min_length=1, max_length=128, description="起始对象API名")
    to_object_name: str = Field(..., min_length=1, max_length=128, description="目标对象API名")
    multiplicity: Literal["many_to_one", "one_to_one"] = Field(
        default="many_to_one", description="基数（多对多走 junction 对象表达）"
    )
    from_columns: list[str] = Field(..., min_length=1, description="起始侧关联列（属性API名）")
    to_columns: list[str] = Field(..., min_length=1, description="目标侧关联列（必须引用对方标识属性）")
    from_display_name: str = Field(default="", max_length=128, description="正向方向展示名")
    to_display_name: str = Field(default="", max_length=128, description="反向方向展示名")
    from_verbalize: str = Field(default="", max_length=512, description='正向自然语言模式串，如 "{from} 归属于 {to}"')
    to_verbalize: str = Field(default="", max_length=512, description="反向自然语言模式串")
    description: str = Field(default="", max_length=1024, description="关系描述")
    business_notes: str = Field(default="", max_length=10000, description="业务说明")
    allow_overwrite: bool = Field(default=False, description="编辑既有关系时为 True")
    confirm_destructive: bool = Field(default=False, description="破坏性变更（连接对象/关联列/基数变更）显式确认")


class DatasourceTableIndexesUpdateRequest(BaseModel):
    """数据源表索引配置更新请求（整体覆盖）"""

    indexes: list[TableIndexItem] = Field(default_factory=list, max_length=5, description="索引列表")


class SkillCreateRequest(BaseModel):
    """用户技能创建请求"""

    skill_name: str = Field(..., min_length=3, max_length=64, description="技能英文名称，使用小写字母和中划线")
    skill_title: str = Field(..., min_length=1, max_length=200, description="技能标题")
    skill_desc: str = Field(default="", max_length=1000, description="技能描述")
    skill_category: str = Field(default="custom", max_length=100, description="技能分类")
    visibility_type: Literal["private", "global"] = Field(default="private", description="可见范围")
    content: str = Field(default="", description="SKILL.md 正文或完整内容")


class SkillUpdateRequest(BaseModel):
    """用户技能更新请求"""

    skill_title: str = Field(..., min_length=1, max_length=200, description="技能标题")
    skill_desc: str = Field(default="", max_length=1000, description="技能描述")
    skill_category: str = Field(default="custom", max_length=100, description="技能分类")
    visibility_type: Literal["private", "global"] = Field(default="private", description="可见范围")
    content: str = Field(default="", description="SKILL.md 正文或完整内容")


class SkillFileUpsertRequest(BaseModel):
    """用户技能版本文件保存请求"""

    dir_name: Literal["references", "assets"] = Field(..., description="技能固定目录")
    file_path: str = Field(..., min_length=1, max_length=500, description="目录内相对路径")
    content: str = Field(default="", description="文件内容")


class SkillImportFromWorkspaceRequest(BaseModel):
    """从智能分析工作区导入用户技能"""

    skill_dir_path: str = Field(..., min_length=1, max_length=500, description="工作区技能目录路径")


class DatasourceSourceItem(BaseModel):
    """数据源连接项"""

    org_name: str = Field(..., min_length=1, max_length=128, description="机构名称")
    org_code: str = Field(..., min_length=1, max_length=128, description="机构编码")
    query_executor_type: QueryExecutorType = Field(..., description="查询执行方式: direct | http")
    host: str = Field(default="", description="主机地址")
    port: int | None = Field(default=None, description="端口")
    database: str = Field(..., min_length=1, description="数据库名")

    @model_validator(mode="after")
    def validate_required_fields(self) -> "DatasourceSourceItem":
        self.org_name = str(self.org_name or "").strip()
        self.org_code = str(self.org_code or "").strip()
        self.host = str(self.host or "").strip()
        self.database = str(self.database or "").strip()
        if not self.org_name:
            raise ValueError("org_name 不能为空")
        if not self.org_code:
            raise ValueError("org_code 不能为空")
        if self.query_executor_type == "direct" and not self.host:
            raise ValueError("host 不能为空")
        if self.query_executor_type == "http" and (self.host or self.port is not None):
            raise ValueError("HTTP 模式只填写数据库名，不填写 IP 和端口")
        if not self.database:
            raise ValueError("database 不能为空")
        return self


class DatasourceGroupCreateRequest(BaseModel):
    """数据源组创建请求"""

    group_name: str = Field(..., min_length=1, max_length=128, description="数据源组名称")
    group_desc: str | None = Field(default=None, description="数据源组描述")
    query_context: str | None = Field(default=None, max_length=20000, description="智能问数说明")
    recommended_questions: list[str] = Field(
        default_factory=list, max_length=MAX_RECOMMENDED_QUESTION_COUNT, description="推荐问题，每条不超过50字"
    )
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    member_sources: list[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")

    @field_validator("recommended_questions", mode="before")
    @classmethod
    def validate_recommended_questions(cls, value: Any) -> list[str]:
        return _normalize_recommended_questions(value)


class DatasourceGroupUpdateRequest(BaseModel):
    """数据源组更新请求"""

    group_name: str = Field(..., min_length=1, max_length=128, description="数据源组名称")
    group_desc: str | None = Field(default=None, description="数据源组描述")
    query_context: str | None = Field(default=None, max_length=20000, description="智能问数说明")
    recommended_questions: list[str] = Field(
        default_factory=list, max_length=MAX_RECOMMENDED_QUESTION_COUNT, description="推荐问题，每条不超过50字"
    )
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    member_sources: list[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")

    @field_validator("recommended_questions", mode="before")
    @classmethod
    def validate_recommended_questions(cls, value: Any) -> list[str]:
        return _normalize_recommended_questions(value)


class DatasourceGroupMetadataSyncRequest(BaseModel):
    """数据源组模板元数据同步请求"""

    template_table_names: list[str] = Field(default_factory=list, description="需要同步的模板表名列表")


# ============ 语义层配置模型 ============


class SemanticMetricPeriod(BaseModel):
    """派生指标固定统计周期"""

    kind: Literal["today", "yesterday", "last_n_days", "this_week", "this_month", "between"] = Field(
        ..., description="周期类型"
    )
    n: int | None = Field(default=None, ge=1, description="last_n_days 的天数 N")
    start: str | None = Field(default=None, description="between 的开始日期 YYYY-MM-DD")
    end: str | None = Field(default=None, description="between 的结束日期 YYYY-MM-DD")
    time_dimension: str | None = Field(default=None, description="用于周期过滤的时间维度名称")


class SemanticMetricFilter(BaseModel):
    """派生指标修饰词过滤"""

    dimension: str = Field(..., description="过滤维度名称")
    operator: str = Field(..., description="操作符，如 '='、'IN'")
    value: str | list[str] = Field(default="", description="筛选值；IN/NOT IN 为字符串数组")

    @field_validator("operator", mode="before")
    @classmethod
    def normalize_operator(cls, value: Any) -> str:
        """操作符统一大写并收敛到 FILTER_OPERATORS 权威集合。"""
        operator = str(value or "=").strip().upper()
        if operator not in FILTER_OPERATORS:
            raise ValueError(f"修饰词过滤操作符不支持: {operator}")
        return operator


class SemanticMetric(BaseModel):
    """语义层指标定义"""

    name: str = Field(..., description="指标名称，如'销售额'")
    field_name: str | None = Field(default=None, description="逻辑字段名（可选，用于区分同一物理字段的多个配置）")
    expression: str = Field(..., description="SQL聚合表达式，如'SUM(amount)'")
    table: str = Field(..., description="所属表名")
    table_alias: str | None = Field(default=None, description="查询表别名；为空时默认等于所属表名")
    data_type: str | None = Field(
        default=None, description="指标结果或源字段类型，如'int'、'decimal(10,2)'、'timestamp'"
    )
    filter: str | None = Field(default=None, description="默认过滤条件，如'is_active=1'")
    description: str | None = Field(default=None, description="指标描述")
    metadata_supplement: str | None = Field(default=None, description="字段级元数据补充说明")
    compare_type: Literal["mom", "yoy"] | None = Field(
        default=None, description="预计算同比/环比指标类型：mom=环比，yoy=同比"
    )
    compare_mode: Literal["trend", "value"] | None = Field(
        default=None, description="预计算同比/环比指标形态：trend=趋势，value=单值"
    )
    base_metric: str | None = Field(
        default=None,
        description="基础指标名称；派生指标为必填（口径来源），预计算同比/环比指标（legacy）亦复用此字段，如'销售额'",
    )
    # 派生/复合指标扩展：metric_kind 缺省（None）即为原子指标
    metric_kind: Literal["derived", "composite"] | None = Field(
        default=None, description="指标类别：derived=派生指标，composite=复合指标，缺省为原子指标"
    )
    period: SemanticMetricPeriod | None = Field(default=None, description="派生指标固定统计周期")
    filters: list[SemanticMetricFilter] | None = Field(default=None, description="派生指标修饰词过滤")
    formula: str | None = Field(default=None, description="复合指标公式，如'{销售额} / {订单数}'")

    @model_validator(mode="after")
    def validate_metric_kind_scoped_fields(self) -> "SemanticMetric":
        """按 metric_kind 分型校验字段归属，避免各类指标口径字段混用。"""
        if self.metric_kind == "derived":
            self._validate_derived_metric_fields()
        elif self.metric_kind == "composite":
            self._validate_composite_metric_fields()
        else:
            self._validate_atomic_metric_fields()
        return self

    def _validate_derived_metric_fields(self) -> None:
        """派生指标：口径由基础指标 + 周期 + 过滤决定，不允许自带 expression。"""
        if not str(self.base_metric or "").strip():
            raise ValueError(f"派生指标 {self.name} 缺少 base_metric 基础指标")
        if str(self.expression or "").strip():
            raise ValueError(f"派生指标 {self.name} 不能配置 expression，口径由基础指标 + 周期 + 过滤决定")

    def _validate_composite_metric_fields(self) -> None:
        """复合指标：口径由 formula 引用的成员决定，不允许 expression / base_metric。"""
        if not str(self.formula or "").strip():
            raise ValueError(f"复合指标 {self.name} 缺少 formula 公式")
        if str(self.expression or "").strip():
            raise ValueError(f"复合指标 {self.name} 不能配置 expression，口径由 formula 决定")
        if str(self.base_metric or "").strip():
            raise ValueError(f"复合指标 {self.name} 不能配置 base_metric，成员由公式引用决定")

    def _validate_atomic_metric_fields(self) -> None:
        """原子指标：必须有 expression，且不得携带派生/复合指标专属字段。"""
        if not str(self.expression or "").strip():
            raise ValueError(f"指标 {self.name} 缺少 expression 聚合表达式")
        if str(self.formula or "").strip():
            raise ValueError(f"原子指标 {self.name} 不能配置 formula（仅复合指标使用）")
        if self.period is not None or self.filters:
            raise ValueError(f"原子指标 {self.name} 不能配置 period/filters（仅派生指标使用）")
        # base_metric 双语义收敛：原子指标上仅允许同比/环比指标使用（旧口径兼容）
        if str(self.base_metric or "").strip() and not (self.compare_type or self.compare_mode):
            raise ValueError(
                f"原子指标 {self.name} 不允许配置 base_metric："
                "仅派生指标或预计算同比/环比指标（需同时配置 compare_type/compare_mode）使用"
            )


class SemanticDimension(BaseModel):
    """语义层维度定义（增强版）"""

    name: str = Field(..., description="维度名称，如'数据库类型'")
    field_name: str | None = Field(default=None, description="逻辑字段名（可选，用于区分同一物理字段的多个配置）")
    column: str = Field(..., description="对应的字段名")
    expression: str | None = Field(default=None, description="SQL表达式，如'DATE(create_time)'")
    table: str = Field(..., description="所属表名")
    table_alias: str | None = Field(default=None, description="查询表别名；为空时默认等于所属表名")
    logical_type: Literal["text", "number", "date", "datetime", "enum"] | None = Field(
        default=None, description="逻辑值类型，用于 DSL 过滤值校验和前端字段类型展示"
    )
    description: str | None = Field(default=None, description="维度描述")
    metadata_supplement: str | None = Field(default=None, description="字段级元数据补充说明")
    mapping: dict[str, str] | None = Field(
        default=None, description="同义词映射（同义词→标准值），如{'mysql':'MySQL', 'pg':'PostgreSQL'}"
    )

    # 新增：过滤增强
    allowed_operators: list[str] | None = Field(
        default=None, description="允许的过滤操作符，如['=', '!=', 'IN', 'NOT IN', '>', '<']，为空则使用默认值"
    )
    allowed_values: list[str] | None = Field(
        default=None, description="预定义的可选值（用于下拉选择），如['MySQL', 'PostgreSQL']"
    )


class SemanticRelationship(BaseModel):
    """语义层表关系定义（改进版 Join）"""

    left_table: str = Field(..., description="左表名")
    right_table: str = Field(..., description="右表名")
    left_alias: str | None = Field(default=None, description="左表查询别名；为空时默认等于左表名")
    right_alias: str | None = Field(default=None, description="右表查询别名；为空时默认等于右表名")
    left_column: str = Field(..., description="左表关联字段")
    right_column: str = Field(..., description="右表关联字段")
    join_conditions: list[dict[str, str]] = Field(
        default_factory=list,
        description="多字段关联条件列表，如[{left_column:'id', right_column:'id'}]；多个条件使用AND",
    )
    relationship_type: Literal["one_to_one", "one_to_many", "many_to_one"] = Field(
        default="one_to_one", description="关系基数：one_to_one/one_to_many/many_to_one"
    )
    join_type: Literal["inner", "left", "right"] = Field(default="left", description="关联类型")


class SemanticConfig(BaseModel):
    """单个语义配置（支持多配置）"""

    id: str = Field(..., description="配置ID")
    name: str = Field(..., description="配置名称，如'数据库集群分析'")
    description: str | None = Field(default=None, description="指标集短描述")
    business_notes: str | None = Field(default=None, description="业务说明/业务知识")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: list[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: list[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: list[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticConfigCreateRequest(BaseModel):
    """创建语义配置请求"""

    name: str = Field(..., description="配置名称")
    description: str | None = Field(default=None, description="指标集短描述")
    business_notes: str | None = Field(default=None, description="业务说明/业务知识")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: list[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: list[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: list[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticOssieImportRequest(BaseModel):
    """Ossie 语义模型导入请求（YAML 全文）"""

    yaml_content: str = Field(..., min_length=1, max_length=2_000_000, description="Ossie YAML 文档全文")
    # 同名冲突处理动作：模型名 → replace（覆盖同名配置内容，强制禁用）/ skip（跳过该模型）。
    # 冲突模型缺省动作时按 skip 处理（保守：不破坏已有配置）；非法值在路由层整体拒绝。
    conflict_actions: dict[str, str] = Field(default_factory=dict, description="同名冲突处理动作: replace/skip")


class SemanticConfigUpdateRequest(BaseModel):
    """更新语义配置请求"""

    name: str | None = Field(default=None, description="配置名称")
    description: str | None = Field(default=None, description="指标集短描述")
    business_notes: str | None = Field(default=None, description="业务说明/业务知识")
    enabled: bool | None = Field(default=None, description="是否启用")
    metrics: list[SemanticMetric] | None = Field(default=None, description="指标列表")
    dimensions: list[SemanticDimension] | None = Field(default=None, description="维度列表")
    relationships: list[SemanticRelationship] | None = Field(default=None, description="表关系列表")


class SemanticConfigBatchToggleRequest(BaseModel):
    """批量切换语义配置启用状态请求"""

    config_ids: list[str] = Field(default_factory=list, description="待切换的指标集 ID 列表")
    enabled: bool = Field(..., description="目标启用状态")


_ALL_LEARNING_SOURCES: list[Literal["agent", "builder", "sql"]] = ["agent", "builder", "sql"]


def _all_learning_sources() -> list[Literal["agent", "builder", "sql"]]:
    return list(_ALL_LEARNING_SOURCES)


class SemanticHistoryLearningRequest(BaseModel):
    """从当前指标集查询历史生成维护建议。"""

    days: int = Field(default=7, ge=1, le=90, description="学习最近多少天的历史")
    limit: int = Field(default=50, ge=1, le=200, description="最大学习历史条数")
    sources: list[Literal["agent", "builder", "sql"]] = Field(
        default_factory=_all_learning_sources, description="学习来源"
    )
    include_failed: bool = Field(default=True, description="是否包含失败的智能问数/可视化查询")


class AccuracyEvaluationRunRequest(BaseModel):
    """智能问数准确率评测请求"""

    datasource_id: str = Field(..., description="数据源 ID")
    semantic_config_id: str = Field(..., description="语义配置 ID")
    model_id: str | None = Field(default=None, description="模型 ID")
    agent_id: Literal["chat_data"] = Field(default="chat_data", description="Agent ID")
    query_mode: str = Field(default="query", description="智能问数模式")
    limit: int | None = Field(default=10, ge=1, le=200, description="最多评测用例数")
    case_ids: list[str] = Field(default_factory=list, description="指定评测用例 ID")
    include_traces: bool = Field(default=False, description="是否返回完整工具轨迹")
    local_compile: bool = Field(default=True, description="是否本地重新编译 DSL")
    skip_execution: bool = Field(default=True, description="是否跳过 SQL 执行")
    request_timeout: float = Field(default=180.0, ge=1, le=600, description="单次问答超时时间")


class AccuracyEvaluationCase(BaseModel):
    """当前场景准确率评测用例。"""

    id: str = Field(..., description="用例 ID")
    nl: str = Field(..., min_length=1, description="自然语言问题")
    gold_dsl: dict[str, Any] = Field(default_factory=dict, description="标准 DSL")
    gold_sql: str = Field(default="", description="标准 SQL")
    query_type: Literal["filter", "join", "aggregation", "subquery"] = Field(
        default="aggregation", description="查询类型"
    )
    difficulty: Literal["easy", "medium", "hard"] = Field(default="easy", description="难度")
    source: Literal["human", "llm"] = Field(default="human", description="来源")
    business_domain: str = Field(..., min_length=1, description="业务域")


class AccuracyEvaluationCasesUpsertRequest(BaseModel):
    """保存评测用例列表请求。"""

    business_domain: str | None = Field(default=None, description="业务域")
    cases: list[AccuracyEvaluationCase] = Field(default_factory=list, description="评测用例列表")


class AccuracyEvaluationDraftCaseRequest(BaseModel):
    """生成单条评测用例草稿请求。"""

    hint: str | None = Field(default=None, description="可选生成方向")
    existing_questions: list[str] = Field(default_factory=list, description="已有问题，用于避免重复")


# ============ 树形过滤条件模型（参考 Cube.js 设计）============

# 使用 ForwardRef 解决循环引用
FilterConditionRef = ForwardRef("FilterCondition")


class LeafFilter(BaseModel):
    """叶子过滤条件（单个维度的过滤）"""

    dimension: str = Field(..., description="维度名称")
    operator: str = Field(default="=", description="操作符：=, !=, >, <, >=, <=, IN, NOT IN, LIKE, NOT LIKE")
    value: str | int | float | list[str | int | float] = Field(..., description="过滤值，IN/NOT IN 操作符使用列表")


class AndFilter(BaseModel):
    """AND 组合过滤条件（所有子条件都要满足）"""

    model_config = {"populate_by_name": True}

    and_: list["FilterCondition"] = Field(..., alias="and", description="子条件列表，所有条件都要满足")


class OrFilter(BaseModel):
    """OR 组合过滤条件（任一子条件满足即可）"""

    model_config = {"populate_by_name": True}

    or_: list["FilterCondition"] = Field(..., alias="or", description="子条件列表，任一条件满足即可")


# FilterCondition 是联合类型：可以是叶子节点、AND 节点或 OR 节点
FilterCondition = LeafFilter | AndFilter | OrFilter

# 更新前向引用
AndFilter.model_rebuild()
OrFilter.model_rebuild()


# ============ 语义工具输入模型 ============


class ExecuteSemanticInput(BaseModel):
    """语义查询执行工具的输入参数（供 LangGraph Agent 工具使用）"""

    model_config = {"extra": "ignore"}
    semantic_config_id: str = Field(
        ...,
        min_length=1,
        description=f"本次 DSL 使用的指标集 ID；语义模型检索方式以 {DATA_ANALYZING_SKILL} 技能为准",
    )
    dsl: dict[str, Any] = Field(
        ...,
        description="""完整语义 DSL 对象，必须包含在 dsl 参数内，不要把 metrics、dimensions、filters 等字段放在工具顶层。
DSL 字段包括 metrics、dimensions、filters、having、order_by、limit、offset。
dimensions 支持简单字符串和 {"name":"时间维度名","time_grain":"month"}。
filters 使用 SQL 风格操作符，如 =、!=、>、>=、IN、LIKE、BETWEEN、IS NULL；支持 and/or 树和 left/right 结构化表达式。
having 只用于聚合结果过滤。""",
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_mode(cls, data: Any) -> Any:
        """显式拒绝已废弃的 mode 参数，同时允许工具运行时注入的内部字段。"""
        if isinstance(data, dict) and "mode" in data:
            raise ValueError("参数 mode 已废弃，请移除 mode 字段")
        return data


# ============ Agent 工具输入模型 ============


class SemanticQueryInput(BaseModel):
    """查询工具输入：常规使用 DSL 模式，无法表达时使用 SQL 模式。"""

    dsl: dict[str, Any] | None = Field(
        default=None,
        description="DSL 模式专用：完整语义 DSL，包含 metrics、dimensions、filters、having、order_by、limit、offset；operator 使用 SQL 风格操作符，不使用 equals/notEquals；与顶层 sql 二选一，不要传 mode",
    )
    sql: str | None = Field(
        default=None,
        description="SQL 模式专用：仅当 DSL 模式明确无法表达查询结构时使用的只读 SQL；直接执行且不调用 validate_dsl 校验；与顶层 dsl 二选一，不要传 mode",
    )
    semantic_config_id: str = Field(
        ...,
        min_length=1,
        description="本次查询使用的指标集 ID，来自物化语义模型的检索结果",
    )
    member_datasource_ids: list[str] = Field(
        ...,
        min_length=1,
        description='本次查询要执行的成员数据源 ID 列表（JSON 字符串数组，单个机构也传数组，如 ["ORG_CODE1"]）；必须从 list_execution_targets 返回的当前可用执行对象中选择',
    )
    intent: str = Field(
        default="",
        description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示",
    )

    @field_validator("dsl", mode="before")
    @classmethod
    def normalize_dsl(cls, value: Any) -> Any:
        if value is None:
            return None
        return _normalize_json_object(value, "dsl")


class _ValidateDslActionPayload(BaseModel):
    """``validate_dsl`` 的入参结构：校验单个查询 DSL，返回编译后 SQL（不执行）。"""

    dsl: dict[str, Any] = Field(..., description="单个查询 DSL 对象")
    semantic_config_id: str = Field(..., min_length=1, description="本次校验使用的语义模型 ID")

    @field_validator("dsl", mode="before")
    @classmethod
    def normalize_dsl(cls, value: Any) -> Any:
        if value is None:
            return None
        return _normalize_json_object(value, "dsl")


class _ValidateDashboardActionPayload(BaseModel):
    """``validate_dashboard`` 的入参结构：批量校验完整 dashboard_spec 的所有卡片。"""

    dsl: dict[str, Any] | list[dict[str, Any]] = Field(
        ...,
        description="包含 dashboard_name 与完整 cards 数组的 dashboard_spec；兼容旧规格的卡片数组形态",
    )
    semantic_config_id: str | None = Field(
        default=None,
        description="语义模型 ID；单张卡片未携带 semantic_config_id 时的缺省值",
    )

    @field_validator("dsl", mode="before")
    @classmethod
    def normalize_dsl(cls, value: Any) -> Any:
        """兼容 JSON 字符串与二次转义；兼容 dashboard 批量卡片数组形态。"""
        if value is None:
            return None
        current = value
        for _ in range(2):
            if not isinstance(current, str):
                break
            try:
                current = json.loads(current.strip())
            except json.JSONDecodeError as exc:
                raise ValueError("dsl 必须是对象/数组或有效的 JSON 字符串") from exc
        if not isinstance(current, (dict, list)):
            raise ValueError("dsl 必须是对象或数组")
        return current


class _CheckYamlActionPayload(BaseModel):
    """``check_yaml`` 的入参结构：语义模型 YAML 交付前自检。

    只收工作区路径、由后端直读文件：避免模型把整份 YAML 复述进参数
    （token 翻倍、复述走样、参数超长），校验对象始终是磁盘上的权威内容。
    """

    file_path: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="会话工作区内 Ossie 语义模型 YAML 文件路径，如 /mnt/user-data/workspace/model.yaml",
    )

    @model_validator(mode="before")
    @classmethod
    def _reject_legacy_yaml_content(cls, data: Any) -> Any:
        """模型仍按旧习惯传 yaml_content 时，给出可执行的改法而不是泛化拒绝。"""
        if isinstance(data, dict) and "yaml_content" in data:
            raise ValueError(
                "check_yaml 不再接受 yaml_content 全文参数，请改传文件路径："
                '{"file_path": "/mnt/user-data/workspace/semantic/<模型文件>.yaml"}'
            )
        return data


class _ListExecutionTargetsActionPayload(BaseModel):
    """``list_execution_targets`` 的入参结构：实时获取当前用户可用执行对象。

    无业务参数：范围完全由服务端会话上下文（datasource_id + user_id）决定，
    模型不可指定、也不可缩小——这是防越权的一部分。
    """


# action -> action_payload 校验模型。新增 action 时在此登记对应 payload 模型，
# 入参校验与 handler 内的解析共用同一张表，保证两处口径一致。
DATA_ANALYZING_ACTION_PAYLOAD_MODELS: dict[str, type[BaseModel]] = {
    "validate_dsl": _ValidateDslActionPayload,
    "validate_dashboard": _ValidateDashboardActionPayload,
    "execute_query": SemanticQueryInput,
    "check_yaml": _CheckYamlActionPayload,
    "list_execution_targets": _ListExecutionTargetsActionPayload,
}


class DataAnalyzingInput(BaseModel):
    """数据分析工具入参：按 ``action`` 分派，``action_payload`` 结构随 action 变化。

    五个 action：``validate_dsl`` / ``validate_dashboard`` 校验（不执行）、
    ``execute_query`` 执行查询（DSL 或 SQL 二选一）、``check_yaml`` 语义模型 YAML 自检、
    ``list_execution_targets`` 实时获取可用执行对象（只读发现通道）。
    action_payload 不平铺成顶层字段（后续扩展 action 只加 payload 模型即可），
    payload 的跨字段校验在工具实现入口经 ``validate_data_analyzing_payload`` 完成——
    不能放进 model_validator：langgraph 的 ToolInvocationError 会过滤 loc 为空的
    校验错误（model_validator 抛错的 loc 恒为 ()），错误详情会被渲染成空白，
    模型与用户都看不到原因；在工具函数体内抛错则走 ToolErrorMiddleware，详情完整可见。
    """

    action: Literal[
        "validate_dsl",
        "validate_dashboard",
        "execute_query",
        "check_yaml",
        "list_execution_targets",
    ] = Field(
        ...,
        description=(
            "操作类型：validate_dsl 校验单个查询 DSL 并返回编译后 SQL（不执行）；"
            "validate_dashboard 批量校验完整 dashboard_spec 的所有卡片；"
            "execute_query 执行查询（DSL 或 SQL 二选一）；check_yaml 语义模型 YAML 交付前自检；"
            "list_execution_targets 实时获取当前用户可用执行对象清单"
        ),
    )
    action_payload: dict[str, Any] = Field(
        ...,
        description=(
            "操作参数，结构随 action 变化："
            'validate_dsl 传 {"dsl": <单个查询DSL>, "semantic_config_id": "..."}；'
            'validate_dashboard 传 {"dsl": <含 dashboard_name 和 cards 的完整 dashboard_spec>, '
            '"semantic_config_id": "卡片缺省值，可选"}；'
            'execute_query 传 {"dsl": <完整语义DSL> 或 "sql": "...", "semantic_config_id": "...", '
            '"member_datasource_ids": ["<ID>"]}（member_datasource_ids 必须是 ID 字符串数组，单个机构也传数组）；'
            'check_yaml 传 {"file_path": "/mnt/user-data/workspace/model.yaml"}；'
            "list_execution_targets 传 {}"
        ),
    )
    intent: str = Field(
        default="",
        description="一句中文说明本次调用目的（不超过50字，写目的不写动作），面向用户展示",
    )


def validate_data_analyzing_payload(action: str, action_payload: dict[str, Any]) -> Any:
    """按 action 校验 ``action_payload``，返回已验证的 payload 模型实例。

    与 handler 内的解析共用 :data:`DATA_ANALYZING_ACTION_PAYLOAD_MODELS` 一张表，
    保证入参校验与 handler 解析口径一致。必须在工具实现入口调用而不是放进
    ``DataAnalyzingInput`` 的 model_validator——原因见该类 docstring。
    """
    payload_model = DATA_ANALYZING_ACTION_PAYLOAD_MODELS.get(action)
    if payload_model is None:
        raise ValueError(f"不支持的 action: {action}")
    try:
        payload = payload_model.model_validate(action_payload)
    except ValidationError as exc:
        detail = "; ".join(
            f"{'.'.join(str(part) for part in error.get('loc', ())) or 'action_payload'}: {error.get('msg', '')}"
            for error in exc.errors()
        )
        hint = ""
        if "member_datasource_ids" in detail:
            hint = '；member_datasource_ids 必须传 ID 字符串数组，如 ["ORG_CODE1"]，不要传对象或裸字符串'
        raise ValueError(f"action={action} 的 action_payload 校验失败：{detail}{hint}") from None
    if action == "execute_query":
        # SemanticQueryInput 只做字段级校验；dsl/sql 二选一在此统一拦截
        query_payload = cast(SemanticQueryInput, payload)
        has_dsl = query_payload.dsl is not None
        has_sql = bool(str(query_payload.sql or "").strip())
        if has_dsl == has_sql:
            raise ValueError("action=execute_query 必须且只能传入 dsl 或 sql 其中一个")
    return payload


class CalculatorItem(BaseModel):
    """单个确定性计算项"""

    name: str | None = Field(default=None, max_length=100, description="计算项名称，用于区分多个结果")
    expression: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="只包含数字、括号和 + - * / 的算术表达式；单位换算时把倍率直接写进表达式",
    )
    decimal_places: int | None = Field(
        default=None,
        ge=0,
        le=12,
        description="需要四舍五入时保留的小数位数；不传则返回计算结果原值",
    )


class GenerateChartInput(BaseModel):
    """图表生成工具的输入参数"""

    result_id: str = Field(description="查询结果ID，从 data_analyzing_tool 的 execute_query 返回的 result_id")
    chart_type: ChartType | None = Field(default=None, description="图表类型。不传则返回数据分析，由你来推荐")
    x_field: str | None = Field(default=None, description="X轴字段名。不传则返回数据分析")
    y_field: str | None = Field(default=None, description="Y轴字段名；关系图时表示终点字段。不传则返回数据分析")
    series_field: str | None = Field(default=None, description="分组字段名（可选），传入则生成多折线图")


class LoadSkillInput(BaseModel):
    """加载 Skill 工具的输入参数"""

    skill_name: str = Field(description=f"要加载的 skill 名称，如 {DATA_ANALYZING_SKILL}")


# ============ Dashboard 模型 ============


class DashboardCreateRequest(BaseModel):
    """创建大屏请求"""

    name: str = Field(..., description="大屏名称")
    description: str | None = Field(default=None, description="大屏描述")
    datasource_id: str | None = Field(default=None, description="数据源ID")
    theme: Literal["light", "dark"] = Field(default="light", description="主题")


class DashboardUpdateRequest(BaseModel):
    """更新大屏请求"""

    name: str | None = Field(default=None, description="大屏名称")
    description: str | None = Field(default=None, description="大屏描述")
    theme: Literal["light", "dark"] | None = Field(default=None, description="主题")
    background_id: str | None = Field(default=None, description="背景图片ID")


class PanelCreateRequest(BaseModel):
    """创建面板请求"""

    name: str | None = Field(default=None, description="面板名称（可选，缺省时服务端使用本地兜底名称）")
    sql: str = Field(..., description="SQL查询语句")
    chart_type: ChartType = Field(default="table", description="图表类型")
    chart_config: dict | None = Field(default=None, description="图表配置")
    layout_x: int | None = Field(default=None, description="布局X位置，不指定则自动放到最下方")
    layout_y: int | None = Field(default=None, description="布局Y位置，不指定则自动放到最下方")
    layout_w: int = Field(default=6, description="布局宽度(1-12)")
    layout_h: int = Field(default=4, description="布局高度")
    refresh_interval: int | None = Field(default=None, description="刷新间隔(秒)")
    source_thread_id: str | None = Field(default=None, description="来源会话ID")
    datasource_id: str | None = Field(default=None, description="数据源ID")
    member_datasource_id: str | None = Field(default=None, description="成员数据源ID")


class DashboardFromSpecRequest(BaseModel):
    """根据 AI 看板规格创建大屏请求"""

    dashboard_spec: dict[str, Any] = Field(..., description="AI 生成并经服务端校验的看板规格")
    datasource_id: str | None = Field(default=None, description="数据源ID")
    semantic_config_id: str | None = Field(default=None, description="语义配置ID")
    conversation_id: str | None = Field(default=None, description="来源会话ID")


class PanelBatchExecuteRequest(BaseModel):
    """批量执行面板请求"""

    panel_ids: list[str] = Field(default_factory=list, description="面板ID列表，为空时执行当前看板全部面板")
    panel_member_datasource_ids: dict[str, str] = Field(
        default_factory=dict, description="按面板指定执行成员的映射，key 为 panel_id，value 为 member_datasource_id"
    )


class PanelNameGenerateRequest(BaseModel):
    """生成面板标题请求"""

    sql: str = Field(..., description="SQL查询语句")
    chart_type: ChartType = Field(default="table", description="图表类型")
    name: str | None = Field(default=None, description="用户显式传入的名称，存在时优先使用")


class PanelUpdateRequest(BaseModel):
    """更新面板请求"""

    name: str | None = Field(default=None, description="面板名称")
    sql: str | None = Field(default=None, description="SQL查询语句")
    chart_type: ChartType | None = Field(default=None, description="图表类型")
    chart_config: dict | None = Field(default=None, description="图表配置")
    layout_x: int | None = Field(default=None, description="布局X位置")
    layout_y: int | None = Field(default=None, description="布局Y位置")
    layout_w: int | None = Field(default=None, description="布局宽度")
    layout_h: int | None = Field(default=None, description="布局高度")
    refresh_interval: int | None = Field(default=None, description="刷新间隔(秒)")
    member_datasource_id: str | None = Field(default=None, description="成员数据源ID")


class LayoutUpdateRequest(BaseModel):
    """批量更新布局请求"""

    layouts: list[dict] = Field(..., description="布局列表 [{'panel_id': 'xxx', 'x': 0, 'y': 0, 'w': 6, 'h': 4}, ...]")
