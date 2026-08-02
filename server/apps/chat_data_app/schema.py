"""
ChatData API 数据模型定义
"""

import json
from typing import Any, Dict, ForwardRef, List, Literal, Optional, Union
from pydantic import BaseModel, Field, field_validator, model_validator
from .chart_types import ChartType
from .services.runtime_config import DEFAULT_ROW_LIMIT

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


def _normalize_recommended_questions(value: Any) -> List[str]:
    if value is None:
        return []
    raw_items = value.splitlines() if isinstance(value, str) else value
    if not isinstance(raw_items, list):
        raise ValueError("recommended_questions 必须是数组")

    result: List[str] = []
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


# ============ NL2SQL 请求模型 ============

class NL2SQLRequest(BaseModel):
    """NL2SQL 请求模型"""
    query: str = Field(..., min_length=1, description="用户的自然语言查询")
    datasource_id: Optional[str] = Field(
        default=None,
        description="指定使用的数据源ID"
    )
    conversation_id: Optional[str] = Field(
        default=None,
        description="对话ID（用于多轮对话）"
    )
    mode: Optional[str] = Field(
        default="generate",
        description="执行模式：generate（仅生成SQL）或 query（生成并执行）"
    )
    semantic_config_id: Optional[str] = Field(
        default=None,
        description="语义配置ID（指定使用哪个语义配置）"
    )

# ============ SQL 编辑器相关模型 ============

class QuickSqlRequest(BaseModel):
    """快速 SQL 生成请求（用于 SQL 编辑器的 AI 辅助）"""
    query: str = Field(..., min_length=1, description="自然语言查询")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    conversation_id: Optional[str] = Field(default=None, description="SQL 辅助会话ID")


class ExecuteSqlRequest(BaseModel):
    """SQL 执行运行时请求（供异步任务、智能体和内部复用）"""
    sql: str = Field(..., min_length=1, description="要执行的 SQL 语句")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: Optional[str] = Field(default=None, description="当前用户ID")
    conversation_id: Optional[str] = Field(default=None, description="来源会话ID")
    query_text: Optional[str] = Field(default=None, description="原始用户问题或查询说明")
    semantic_config_id: Optional[str] = Field(default=None, description="语义配置ID")
    limit: Optional[int] = Field(default=DEFAULT_ROW_LIMIT, description="结果行数限制")
    timeout: Optional[int] = Field(default=None, description="查询超时时间（秒），为空时按数据源类型取全局默认值")
    query_type: Optional[str] = Field(default="sql", description="内部查询类型: sql | builder | agent")
    builder_config: Optional[dict] = Field(default=None, description="辅助查询模式的配置")
    semantic_builder_config: Optional[dict] = Field(default=None, description="语义可视化查询 DSL；服务端据此重新编译 SQL")
    skip_query_cache: bool = Field(default=False, description="是否跳过 SQL 查询结果缓存")


class QueryTaskCreateRequest(BaseModel):
    """异步查询任务创建请求"""
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: Optional[str] = Field(default=None, description="当前用户ID")
    operator_name: Optional[str] = Field(default=None, description="当前操作人名称")
    conversation_id: Optional[str] = Field(default=None, description="来源会话ID")
    query_text: Optional[str] = Field(default=None, description="原始用户问题或查询说明")
    semantic_config_id: Optional[str] = Field(default=None, description="语义配置ID")
    sql: List[str] = Field(default_factory=list, description="要执行的 SQL 语句列表；builder 查询由服务端根据 DSL 编译")
    limit: Optional[int] = Field(default=DEFAULT_ROW_LIMIT, description="结果行数限制")
    timeout: Optional[int] = Field(default=None, description="查询超时时间（秒），为空时按数据源类型取全局默认值")
    query_type: Literal["sql", "builder"] = Field(default="sql", description="查询类型: sql | builder")
    builder_config: Optional[dict] = Field(default=None, description="辅助查询模式的配置")
    semantic_builder_config: Optional[dict] = Field(default=None, description="语义可视化查询 DSL；服务端据此重新编译 SQL")
    skip_query_cache: bool = Field(default=False, description="是否跳过 SQL 查询结果缓存")

    @model_validator(mode="after")
    def validate_sql_list(self):
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

    query_type: Literal["sql", "builder", "agent"] = Field(default="sql", description="内部查询类型")


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
    builder_config: Optional[dict] = Field(default=None, description="可视化查询配置（仅 builder 类型）")
    semantic_config_id: Optional[str] = Field(default=None, description="语义配置ID")


class QueryFavoriteUpdateRequest(BaseModel):
    """更新查询收藏请求"""
    favorite_title: Optional[str] = Field(default=None, min_length=1, max_length=255, description="收藏标题")
    query_text: Optional[str] = Field(default=None, min_length=1, description="原始查询内容（问题或SQL）")
    builder_config: Optional[dict] = Field(default=None, description="可视化查询配置（仅 builder 类型）")
    semantic_config_id: Optional[str] = Field(default=None, description="语义配置ID")

# ============ 表管理相关模型 ============

class TableListResponse(BaseModel):
    """表列表响应"""
    status: Literal["success", "error"] = "success"
    msg: str = ""
    code: int = 2000
    data: Optional[Dict[str, List[str]]] = Field(
        default=None,
        description="表名列表 {'tables': ['table1', 'table2']}"
    )


class TableInfoRequest(BaseModel):
    """表信息请求"""
    table_name: str = Field(..., description="表名")
    include_ddl: bool = Field(default=False, description="是否包含DDL语句")


class TableInfoResult(BaseModel):
    """表信息结果"""
    table_name: str = Field(..., description="表名")
    description: str = Field(default="", description="表描述")
    columns: List[Dict] = Field(default_factory=list, description="列信息列表")
    ddl: Optional[str] = Field(default=None, description="DDL语句")


class TableInfoResponse(BaseModel):
    """表信息响应"""
    status: Literal["success", "error"] = "success"
    msg: str = ""
    code: int = 2000
    data: Optional[TableInfoResult] = None


# ============ 数据库元数据同步模型 ============

class DbConnectionRequest(BaseModel):
    """数据库连接请求（用于获取表列表）"""
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    query_executor_type: QueryExecutorType = Field(default="direct", description="查询执行方式: direct | http")
    host: str = Field(default="", description="服务器地址")
    port: Optional[int] = Field(default=None, description="端口（Hive默认10000，MySQL默认3306）")
    database: str = Field(..., description="数据库名")
    org_code: Optional[str] = Field(default=None, description="机构编码")
    org_name: Optional[str] = Field(default=None, description="机构名称")

    @model_validator(mode="after")
    def validate_connection_fields(self):
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
    name: Optional[str] = Field(default=None, description="数据源名称")
    description: Optional[str] = Field(default=None, description="数据源描述")


class DatasourceTableGroupItem(BaseModel):
    """数据源表分组项"""
    table_group_name: str = Field(..., min_length=1, max_length=100, description="表分组名称")
    table_names: List[str] = Field(default_factory=list, description="分组内表名列表")


class DatasourceTableGroupsUpdateRequest(BaseModel):
    """数据源表分组更新请求"""
    groups: List[DatasourceTableGroupItem] = Field(default_factory=list, description="表分组列表")


class DatasourceTableBusinessDescUpdateRequest(BaseModel):
    """数据源表业务说明更新请求"""
    business_desc: str = Field(default="", max_length=255, description="表业务说明")


class DatasourceColumnDictEnumItem(BaseModel):
    """数据源字段字典枚举项"""
    value: str = Field(..., min_length=1, max_length=128, description="枚举值")
    label: str = Field(default="", max_length=255, description="枚举含义")


class DatasourceColumnDictPayload(BaseModel):
    """数据源字段字典内容"""
    enum_values: List[DatasourceColumnDictEnumItem] = Field(default_factory=list, description="枚举值列表")
    synonyms: List[str] = Field(default_factory=list, description="字段同义词")
    storage_format: str = Field(default="", max_length=255, description="数据存储格式")
    unit: str = Field(default="", max_length=100, description="数值单位")


class DatasourceColumnDictUpdateRequest(BaseModel):
    """数据源字段字典更新请求"""
    model_config = {"populate_by_name": True}

    column_dict: DatasourceColumnDictPayload = Field(default_factory=DatasourceColumnDictPayload, alias="dict", description="字段字典")


class DatasourceColumnDictGenerateRequest(BaseModel):
    """数据源字段字典生成请求"""
    text: str = Field(..., min_length=1, max_length=10000, description="用于生成字段字典的文本")


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
    dir_name: Literal["references", "assets", "scripts"] = Field(..., description="技能固定目录")
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
    port: Optional[int] = Field(default=None, description="端口")
    database: str = Field(..., min_length=1, description="数据库名")

    @model_validator(mode="after")
    def validate_required_fields(self):
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
    group_desc: Optional[str] = Field(default=None, description="数据源组描述")
    query_context: Optional[str] = Field(default=None, max_length=20000, description="智能问数说明")
    recommended_questions: List[str] = Field(default_factory=list, max_length=MAX_RECOMMENDED_QUESTION_COUNT, description="推荐问题，每条不超过50字")
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    member_sources: List[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")

    @field_validator("recommended_questions", mode="before")
    @classmethod
    def validate_recommended_questions(cls, value: Any) -> List[str]:
        return _normalize_recommended_questions(value)


class DatasourceGroupUpdateRequest(BaseModel):
    """数据源组更新请求"""
    group_name: str = Field(..., min_length=1, max_length=128, description="数据源组名称")
    group_desc: Optional[str] = Field(default=None, description="数据源组描述")
    query_context: Optional[str] = Field(default=None, max_length=20000, description="智能问数说明")
    recommended_questions: List[str] = Field(default_factory=list, max_length=MAX_RECOMMENDED_QUESTION_COUNT, description="推荐问题，每条不超过50字")
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    member_sources: List[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")

    @field_validator("recommended_questions", mode="before")
    @classmethod
    def validate_recommended_questions(cls, value: Any) -> List[str]:
        return _normalize_recommended_questions(value)


class DatasourceGroupMetadataSyncRequest(BaseModel):
    """数据源组模板元数据同步请求"""
    template_table_names: List[str] = Field(default_factory=list, description="需要同步的模板表名列表")


# ============ 语义层配置模型 ============

class SemanticMetric(BaseModel):
    """语义层指标定义"""
    name: str = Field(..., description="指标名称，如'销售额'")
    field_name: Optional[str] = Field(default=None, description="逻辑字段名（可选，用于区分同一物理字段的多个配置）")
    expression: str = Field(..., description="SQL聚合表达式，如'SUM(amount)'")
    table: str = Field(..., description="所属表名")
    table_alias: Optional[str] = Field(default=None, description="查询表别名；为空时默认等于所属表名")
    data_type: Optional[str] = Field(default=None, description="指标结果或源字段类型，如'int'、'decimal(10,2)'、'timestamp'")
    filter: Optional[str] = Field(default=None, description="默认过滤条件，如'is_active=1'")
    description: Optional[str] = Field(default=None, description="指标描述")
    metadata_supplement: Optional[str] = Field(default=None, description="字段级元数据补充说明")
    compare_type: Optional[Literal["mom", "yoy"]] = Field(default=None, description="预计算同比/环比指标类型：mom=环比，yoy=同比")
    compare_mode: Optional[Literal["trend", "value"]] = Field(default=None, description="预计算同比/环比指标形态：trend=趋势，value=单值")
    base_metric: Optional[str] = Field(default=None, description="预计算同比/环比指标对应的基础指标名称，如'销售额'")


class SemanticDimension(BaseModel):
    """语义层维度定义（增强版）"""
    name: str = Field(..., description="维度名称，如'数据库类型'")
    field_name: Optional[str] = Field(default=None, description="逻辑字段名（可选，用于区分同一物理字段的多个配置）")
    column: str = Field(..., description="对应的字段名")
    expression: Optional[str] = Field(default=None, description="SQL表达式，如'DATE(create_time)'")
    table: str = Field(..., description="所属表名")
    table_alias: Optional[str] = Field(default=None, description="查询表别名；为空时默认等于所属表名")
    logical_type: Optional[Literal["text", "number", "date", "datetime", "enum"]] = Field(
        default=None,
        description="逻辑值类型，用于 DSL 过滤值校验和前端字段类型展示"
    )
    description: Optional[str] = Field(default=None, description="维度描述")
    metadata_supplement: Optional[str] = Field(default=None, description="字段级元数据补充说明")
    mapping: Optional[Dict[str, str]] = Field(default=None, description="同义词映射（同义词→标准值），如{'mysql':'MySQL', 'pg':'PostgreSQL'}")

    # 新增：过滤增强
    allowed_operators: Optional[List[str]] = Field(
        default=None,
        description="允许的过滤操作符，如['=', '!=', 'IN', 'NOT IN', '>', '<']，为空则使用默认值"
    )
    allowed_values: Optional[List[str]] = Field(
        default=None,
        description="预定义的可选值（用于下拉选择），如['MySQL', 'PostgreSQL']"
    )


class SemanticRelationship(BaseModel):
    """语义层表关系定义（改进版 Join）"""
    left_table: str = Field(..., description="左表名")
    right_table: str = Field(..., description="右表名")
    left_alias: Optional[str] = Field(default=None, description="左表查询别名；为空时默认等于左表名")
    right_alias: Optional[str] = Field(default=None, description="右表查询别名；为空时默认等于右表名")
    left_column: str = Field(..., description="左表关联字段")
    right_column: str = Field(..., description="右表关联字段")
    join_conditions: List[Dict[str, str]] = Field(
        default_factory=list,
        description="多字段关联条件列表，如[{left_column:'id', right_column:'id'}]；多个条件使用AND"
    )
    relationship_type: Literal["one_to_one", "one_to_many", "many_to_one"] = Field(
        default="one_to_many",
        description="关系类型：one_to_one/one_to_many/many_to_one"
    )
    join_type: Literal["inner", "left", "right"] = Field(default="left", description="关联类型")


class SemanticConfig(BaseModel):
    """单个语义配置（支持多配置）"""
    id: str = Field(..., description="配置ID")
    name: str = Field(..., description="配置名称，如'数据库集群分析'")
    description: Optional[str] = Field(default=None, description="指标集短描述")
    ontology_rules: Optional[str] = Field(default=None, description="业务描述/本体规则")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: List[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: List[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: List[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticConfigCreateRequest(BaseModel):
    """创建语义配置请求"""
    name: str = Field(..., description="配置名称")
    description: Optional[str] = Field(default=None, description="指标集短描述")
    ontology_rules: Optional[str] = Field(default=None, description="业务描述/本体规则")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: List[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: List[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: List[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticConfigUpdateRequest(BaseModel):
    """更新语义配置请求"""
    name: Optional[str] = Field(default=None, description="配置名称")
    description: Optional[str] = Field(default=None, description="指标集短描述")
    ontology_rules: Optional[str] = Field(default=None, description="业务描述/本体规则")
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    metrics: Optional[List[SemanticMetric]] = Field(default=None, description="指标列表")
    dimensions: Optional[List[SemanticDimension]] = Field(default=None, description="维度列表")
    relationships: Optional[List[SemanticRelationship]] = Field(default=None, description="表关系列表")


class SemanticHistoryLearningRequest(BaseModel):
    """从当前指标集查询历史生成维护建议。"""
    days: int = Field(default=7, ge=1, le=90, description="学习最近多少天的历史")
    limit: int = Field(default=50, ge=1, le=200, description="最大学习历史条数")
    sources: List[Literal["agent", "builder", "sql"]] = Field(default_factory=lambda: ["agent", "builder", "sql"], description="学习来源")
    include_failed: bool = Field(default=True, description="是否包含失败的智能问数/可视化查询")


class AccuracyEvaluationRunRequest(BaseModel):
    """智能问数准确率评测请求"""
    datasource_id: str = Field(..., description="数据源 ID")
    semantic_config_id: str = Field(..., description="语义配置 ID")
    model_id: Optional[str] = Field(default=None, description="模型 ID")
    agent_id: Literal["chat_data"] = Field(default="chat_data", description="Agent ID")
    query_mode: str = Field(default="query", description="智能问数模式")
    limit: Optional[int] = Field(default=10, ge=1, le=200, description="最多评测用例数")
    case_ids: List[str] = Field(default_factory=list, description="指定评测用例 ID")
    include_traces: bool = Field(default=False, description="是否返回完整工具轨迹")
    local_compile: bool = Field(default=True, description="是否本地重新编译 DSL")
    skip_execution: bool = Field(default=True, description="是否跳过 SQL 执行")
    request_timeout: float = Field(default=180.0, ge=1, le=600, description="单次问答超时时间")


class AccuracyEvaluationCase(BaseModel):
    """当前场景准确率评测用例。"""
    id: str = Field(..., description="用例 ID")
    nl: str = Field(..., min_length=1, description="自然语言问题")
    gold_dsl: Dict[str, Any] = Field(default_factory=dict, description="标准 DSL")
    gold_sql: str = Field(default="", description="标准 SQL")
    query_type: Literal["filter", "join", "aggregation", "subquery"] = Field(default="aggregation", description="查询类型")
    difficulty: Literal["easy", "medium", "hard"] = Field(default="easy", description="难度")
    source: Literal["human", "llm"] = Field(default="human", description="来源")
    business_domain: str = Field(..., min_length=1, description="业务域")


class AccuracyEvaluationCasesUpsertRequest(BaseModel):
    """保存评测用例列表请求。"""
    business_domain: Optional[str] = Field(default=None, description="业务域")
    cases: List[AccuracyEvaluationCase] = Field(default_factory=list, description="评测用例列表")


class AccuracyEvaluationDraftCaseRequest(BaseModel):
    """生成单条评测用例草稿请求。"""
    hint: Optional[str] = Field(default=None, description="可选生成方向")
    existing_questions: List[str] = Field(default_factory=list, description="已有问题，用于避免重复")


class SemanticAutoPrefillRequest(BaseModel):
    """语义字段自动预填请求"""
    selected_tables: List[str] = Field(default_factory=list, description="当前指标集已选择的数据表")
    table_model_type: Literal["detail", "aggregated"] = Field(default="detail", description="表建模类型：detail 明细表，aggregated 预聚合表")
    generate_business_flow: bool = Field(default=False, description="是否生成业务运转说明")
    business_context: Optional[str] = Field(default=None, description="本次自动预填的业务补充上下文")


class SemanticPrefillColumn(BaseModel):
    """语义预填列信息"""
    name: str = Field(..., description="字段名")
    type: Optional[str] = Field(default=None, description="字段类型")
    mysql_type: Optional[str] = Field(default=None, description="MySQL 字段类型")
    hive_type: Optional[str] = Field(default=None, description="Hive 字段类型")
    description: Optional[str] = Field(default=None, description="字段描述")


class SemanticPrefillTable(BaseModel):
    """语义预填表信息"""
    table_name: str = Field(..., description="表名")
    table_alias: Optional[str] = Field(default=None, description="当前指标集中的表角色别名")
    description: Optional[str] = Field(default=None, description="表描述")
    columns: List[SemanticPrefillColumn] = Field(default_factory=list, description="字段列表")


class SemanticPrefillRequest(BaseModel):
    """语义字段预填请求（短路由版）"""
    datasource_id: str = Field(..., description="数据源 ID")
    config_id: str = Field(..., description="语义配置 ID")
    tables: List[SemanticPrefillTable] = Field(default_factory=list, description="当前待预填的数据表列表")
    table_model_type: Literal["detail", "aggregated"] = Field(default="detail", description="表建模类型：detail 明细表，aggregated 预聚合表")
    generate_business_flow: bool = Field(default=False, description="是否生成业务运转说明")
    business_context: Optional[str] = Field(default=None, description="本次自动预填的业务补充上下文")


# ============ 树形过滤条件模型（参考 Cube.js 设计）============

# 使用 ForwardRef 解决循环引用
FilterConditionRef = ForwardRef('FilterCondition')


class LeafFilter(BaseModel):
    """叶子过滤条件（单个维度的过滤）"""
    dimension: str = Field(..., description="维度名称")
    operator: str = Field(
        default="=",
        description="操作符：=, !=, >, <, >=, <=, IN, NOT IN, LIKE, NOT LIKE"
    )
    value: Union[str, int, float, List[Union[str, int, float]]] = Field(
        ...,
        description="过滤值，IN/NOT IN 操作符使用列表"
    )


class AndFilter(BaseModel):
    """AND 组合过滤条件（所有子条件都要满足）"""
    model_config = {"populate_by_name": True}

    and_: List['FilterCondition'] = Field(
        ...,
        alias="and",
        description="子条件列表，所有条件都要满足"
    )


class OrFilter(BaseModel):
    """OR 组合过滤条件（任一子条件满足即可）"""
    model_config = {"populate_by_name": True}

    or_: List['FilterCondition'] = Field(
        ...,
        alias="or",
        description="子条件列表，任一条件满足即可"
    )


# FilterCondition 是联合类型：可以是叶子节点、AND 节点或 OR 节点
FilterCondition = Union[LeafFilter, AndFilter, OrFilter]

# 更新前向引用
AndFilter.model_rebuild()
OrFilter.model_rebuild()


# ============ 语义工具输入模型 ============

class SelectSemanticConfigInput(BaseModel):
    """指标集搜索工具的输入参数"""
    query: Optional[str] = Field(
        default=None,
        description="用户问题；可包含只读技能路由后追加的简短业务限定、查询类型或粒度要求；不传时使用当前会话最近一条用户消息",
    )


class ExecuteSemanticInput(BaseModel):
    """语义查询执行工具的输入参数（供 LangGraph Agent 工具使用）"""
    model_config = {"extra": "ignore"}
    semantic_config_id: str = Field(
        ...,
        min_length=1,
        description="本次 DSL 使用的指标集 ID，必须来自本轮 search_dataset 返回结果",
    )
    dsl: Dict[str, Any] = Field(
        ...,
        description='''完整语义 DSL 对象，必须包含在 dsl 参数内，不要把 metrics、dimensions、filters 等字段放在工具顶层。
DSL 字段包括 metrics、dimensions、filters、having、order_by、limit、offset。
dimensions 支持简单字符串和 {"name":"时间维度名","time_grain":"month"}。
filters 使用 SQL 风格操作符，如 =、!=、>、>=、IN、LIKE、BETWEEN、IS NULL；支持 and/or 树和 left/right 结构化表达式。
having 只用于聚合结果过滤。'''
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_mode(cls, data: Any):
        """显式拒绝已废弃的 mode 参数，同时允许工具运行时注入的内部字段。"""
        if isinstance(data, dict) and "mode" in data:
            raise ValueError("参数 mode 已废弃，请移除 mode 字段")
        return data


# ============ Agent 工具输入模型 ============

class SemanticQueryInput(BaseModel):
    """查询工具的输入参数。优先接收 DSL；DSL 无法表达时接收只读 SQL。"""
    dsl: Optional[Dict[str, Any]] = Field(
        default=None,
        description="语义查询 DSL，包含 metrics、dimensions、filters、having、order_by、limit、offset；operator 使用 SQL 风格操作符，不使用 equals/notEquals；与 sql 二选一",
    )
    sql: Optional[str] = Field(
        default=None,
        description="DSL 明确无法表达时使用的只读 SQL；与 dsl 二选一",
    )
    semantic_config_id: str = Field(
        ...,
        min_length=1,
        description="本次查询使用的指标集 ID，必须来自本轮 search_dataset 返回结果",
    )
    member_datasource_ids: List[str] = Field(
        ...,
        min_length=1,
        description="本次查询要执行的成员数据源 ID 列表；必须从系统提示词列出的当前可用执行对象中选择",
    )
    save_as: Optional[str] = Field(
        default=None,
        description="可选：将完整查询结果写入当前会话 workspace/ 下的 .json 文件",
    )

    @field_validator("dsl", mode="before")
    @classmethod
    def normalize_dsl(cls, value: Any) -> Any:
        if value is None:
            return None
        return _normalize_json_object(value, "dsl")


class CalculatorItem(BaseModel):
    """单个确定性计算项"""
    name: Optional[str] = Field(default=None, max_length=100, description="计算项名称，用于区分多个结果")
    expression: str = Field(
        ...,
        min_length=1,
        max_length=500,
        description="只包含数字、括号和 + - * / 的算术表达式；单位换算时把倍率直接写进表达式",
    )
    decimal_places: Optional[int] = Field(
        default=None,
        ge=0,
        le=12,
        description="需要四舍五入时保留的小数位数；不传则返回计算结果原值",
    )


class CalculatorInput(BaseModel):
    """确定性计算工具的输入参数"""
    calculations: List[CalculatorItem] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="批量计算项；单个计算也只传一条记录",
    )


class GenerateChartInput(BaseModel):
    """图表生成工具的输入参数"""
    result_id: str = Field(
        description="查询结果ID，从 semantic_query_tool 返回的 result_id"
    )
    chart_type: Optional[ChartType] = Field(
        default=None,
        description="图表类型。不传则返回数据分析，由你来推荐"
    )
    x_field: Optional[str] = Field(
        default=None,
        description="X轴字段名。不传则返回数据分析"
    )
    y_field: Optional[str] = Field(
        default=None,
        description="Y轴字段名；关系图时表示终点字段。不传则返回数据分析"
    )
    series_field: Optional[str] = Field(
        default=None,
        description="分组字段名（可选），传入则生成多折线图"
    )


class LoadSkillInput(BaseModel):
    """加载 Skill 工具的输入参数"""
    skill_name: str = Field(
        description="要加载的 skill 名称，如 dashboard-generation"
    )


# ============ Dashboard 模型 ============

class DashboardCreateRequest(BaseModel):
    """创建大屏请求"""
    name: str = Field(..., description="大屏名称")
    description: Optional[str] = Field(default=None, description="大屏描述")
    datasource_id: Optional[str] = Field(default=None, description="数据源ID")
    theme: Literal["light", "dark"] = Field(default="light", description="主题")


class DashboardUpdateRequest(BaseModel):
    """更新大屏请求"""
    name: Optional[str] = Field(default=None, description="大屏名称")
    description: Optional[str] = Field(default=None, description="大屏描述")
    theme: Optional[Literal["light", "dark"]] = Field(default=None, description="主题")
    background_id: Optional[str] = Field(default=None, description="背景图片ID")


class PanelCreateRequest(BaseModel):
    """创建面板请求"""
    name: Optional[str] = Field(default=None, description="面板名称（可选，缺省时服务端使用本地兜底名称）")
    sql: str = Field(..., description="SQL查询语句")
    chart_type: ChartType = Field(
        default="table",
        description="图表类型"
    )
    chart_config: Optional[Dict] = Field(default=None, description="图表配置")
    layout_x: Optional[int] = Field(default=None, description="布局X位置，不指定则自动放到最下方")
    layout_y: Optional[int] = Field(default=None, description="布局Y位置，不指定则自动放到最下方")
    layout_w: int = Field(default=6, description="布局宽度(1-12)")
    layout_h: int = Field(default=4, description="布局高度")
    refresh_interval: Optional[int] = Field(default=None, description="刷新间隔(秒)")
    source_thread_id: Optional[str] = Field(default=None, description="来源会话ID")
    datasource_id: Optional[str] = Field(default=None, description="数据源ID")
    member_datasource_id: Optional[str] = Field(default=None, description="成员数据源ID")


class DashboardFromSpecRequest(BaseModel):
    """根据 AI 看板规格创建大屏请求"""
    dashboard_spec: Dict[str, Any] = Field(..., description="AI 生成并经服务端校验的看板规格")
    datasource_id: Optional[str] = Field(default=None, description="数据源ID")
    semantic_config_id: Optional[str] = Field(default=None, description="语义配置ID")
    conversation_id: Optional[str] = Field(default=None, description="来源会话ID")


class PanelExecuteRequest(BaseModel):
    """执行单个面板请求"""
    member_datasource_id: Optional[str] = Field(default=None, description="指定执行的成员数据源ID")


class PanelBatchExecuteRequest(BaseModel):
    """批量执行面板请求"""
    panel_ids: List[str] = Field(default_factory=list, description="面板ID列表，为空时执行当前看板全部面板")
    panel_member_datasource_ids: Dict[str, str] = Field(
        default_factory=dict,
        description="按面板指定执行成员的映射，key 为 panel_id，value 为 member_datasource_id"
    )


class PanelNameGenerateRequest(BaseModel):
    """生成面板标题请求"""
    sql: str = Field(..., description="SQL查询语句")
    chart_type: ChartType = Field(
        default="table",
        description="图表类型"
    )
    name: Optional[str] = Field(default=None, description="用户显式传入的名称，存在时优先使用")


class PanelUpdateRequest(BaseModel):
    """更新面板请求"""
    name: Optional[str] = Field(default=None, description="面板名称")
    sql: Optional[str] = Field(default=None, description="SQL查询语句")
    chart_type: Optional[ChartType] = Field(
        default=None,
        description="图表类型"
    )
    chart_config: Optional[Dict] = Field(default=None, description="图表配置")
    layout_x: Optional[int] = Field(default=None, description="布局X位置")
    layout_y: Optional[int] = Field(default=None, description="布局Y位置")
    layout_w: Optional[int] = Field(default=None, description="布局宽度")
    layout_h: Optional[int] = Field(default=None, description="布局高度")
    refresh_interval: Optional[int] = Field(default=None, description="刷新间隔(秒)")
    member_datasource_id: Optional[str] = Field(default=None, description="成员数据源ID")


class LayoutUpdateRequest(BaseModel):
    """批量更新布局请求"""
    layouts: List[Dict] = Field(
        ...,
        description="布局列表 [{'panel_id': 'xxx', 'x': 0, 'y': 0, 'w': 6, 'h': 4}, ...]"
    )
