"""
ChatData API 数据模型定义
"""

from typing import Optional, Dict, List, Literal, Union, Any
from pydantic import BaseModel, Field, model_validator

QueryExecutorType = Literal["direct", "http"]


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
    semantic_config: Optional[dict] = Field(
        default=None,
        description="临时语义配置（用于测试，不会保存到数据库）"
    )


# 注意：ContinueSQLRequest 已废弃，使用公共接口 /api/conversations/{id}/resume


# ============ SQL 编辑器相关模型 ============

class QuickSqlRequest(BaseModel):
    """快速 SQL 生成请求（用于 SQL 编辑器的 AI 辅助）"""
    query: str = Field(..., min_length=1, description="自然语言查询")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")


class ExecuteSqlRequest(BaseModel):
    """直接执行 SQL 请求（用于 SQL 编辑器模式和辅助查询模式）"""
    sql: str = Field(..., min_length=1, description="要执行的 SQL 语句")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: Optional[str] = Field(default=None, description="当前用户ID")
    limit: Optional[int] = Field(default=100, description="结果行数限制")
    timeout: Optional[int] = Field(default=None, description="查询超时时间（秒），为空时按数据源类型取全局默认值")
    query_type: Optional[str] = Field(default='sql', description="查询类型: sql | builder")
    builder_config: Optional[dict] = Field(default=None, description="辅助查询模式的配置")


class QueryTaskCreateRequest(BaseModel):
    """异步查询任务创建请求"""
    task_type: Literal["sql"] = Field(default="sql", description="任务类型")
    datasource_id: str = Field(..., description="数据源ID")
    member_datasource_ids: List[str] = Field(default_factory=list, description="选中的成员数据源ID列表")
    user_id: Optional[str] = Field(default=None, description="当前用户ID")
    sql: str = Field(..., min_length=1, description="要执行的 SQL 语句")
    limit: Optional[int] = Field(default=100, description="结果行数限制")
    timeout: Optional[int] = Field(default=None, description="查询超时时间（秒），为空时按数据源类型取全局默认值")
    query_type: Optional[str] = Field(default='sql', description="查询类型: sql | builder")
    builder_config: Optional[dict] = Field(default=None, description="辅助查询模式的配置")


class CompileBuilderSqlRequest(BaseModel):
    """编译 Builder DSL 请求（仅生成 SQL，不执行）"""
    builder_config: dict = Field(..., description="辅助查询模式的 DSL 配置")
    datasource_id: Optional[str] = Field(default=None, description="数据源ID（用于选择 SQL 方言）")


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
    host: str = Field(..., description="服务器地址")
    port: Optional[int] = Field(default=None, description="端口（Hive默认10000，MySQL默认3306）")
    database: str = Field(..., description="数据库名")


# ============ 数据源刷新请求模型 ============

class DatasourceRefreshRequest(BaseModel):
    """数据源刷新请求"""
    table_names: Optional[List[str]] = Field(
        default=None,
        description="要同步的表名列表（用于增减表）"
    )


class DatasourceUpdateRequest(BaseModel):
    """数据源更新请求（元信息）"""
    name: Optional[str] = Field(default=None, description="数据源名称")
    description: Optional[str] = Field(default=None, description="数据源描述")


class DatasourceSourceItem(BaseModel):
    """数据源连接项"""
    datasource_name: str = Field(..., min_length=1, max_length=128, description="数据源显示名称")
    query_executor_type: QueryExecutorType = Field(..., description="查询执行方式: direct | http")
    host: str = Field(..., min_length=1, description="主机地址")
    port: Optional[int] = Field(default=None, description="端口")
    database: str = Field(..., min_length=1, description="数据库名")

    @model_validator(mode="after")
    def validate_required_fields(self):
        self.datasource_name = str(self.datasource_name or "").strip()
        self.host = str(self.host or "").strip()
        self.database = str(self.database or "").strip()
        if not self.datasource_name:
            raise ValueError("datasource_name 不能为空")
        if not self.host:
            raise ValueError("host 不能为空")
        if not self.database:
            raise ValueError("database 不能为空")
        return self


class DatasourceGroupCreateRequest(BaseModel):
    """数据源组创建请求"""
    group_name: str = Field(..., min_length=1, max_length=128, description="数据源组名称")
    group_desc: Optional[str] = Field(default=None, description="数据源组描述")
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    template_table_names: List[str] = Field(default_factory=list, description="模板数据源选中的表名列表")
    member_sources: List[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")


class DatasourceGroupUpdateRequest(BaseModel):
    """数据源组更新请求"""
    group_name: str = Field(..., min_length=1, max_length=128, description="数据源组名称")
    group_desc: Optional[str] = Field(default=None, description="数据源组描述")
    source_type: Literal["hive", "mysql"] = Field(..., description="数据源类型")
    template_source: DatasourceSourceItem = Field(..., description="模板数据源连接信息")
    template_table_names: List[str] = Field(default_factory=list, description="模板数据源选中的表名列表")
    member_sources: List[DatasourceSourceItem] = Field(default_factory=list, description="组内其他成员数据源连接信息")


# ============ 语义层配置模型 ============

class SemanticMetric(BaseModel):
    """语义层指标定义"""
    name: str = Field(..., description="指标名称，如'销售额'")
    field_name: Optional[str] = Field(default=None, description="逻辑字段名（可选，用于区分同一物理字段的多个配置）")
    expression: str = Field(..., description="SQL聚合表达式，如'SUM(amount)'")
    table: str = Field(..., description="所属表名")
    data_type: Optional[str] = Field(default=None, description="指标结果或源字段类型，如'int'、'decimal(10,2)'、'timestamp'")
    filter: Optional[str] = Field(default=None, description="默认过滤条件，如'is_active=1'")
    description: Optional[str] = Field(default=None, description="指标描述")
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
    type: Literal["categorical", "time", "number"] = Field(default="categorical", description="维度类型")
    description: Optional[str] = Field(default=None, description="维度描述")
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
    left_table: str = Field(..., description="左表名（主表）")
    right_table: str = Field(..., description="右表名（关联表）")
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
    description: Optional[str] = Field(default=None, description="配置描述")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: List[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: List[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: List[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticConfigCreateRequest(BaseModel):
    """创建语义配置请求"""
    name: str = Field(..., description="配置名称")
    description: Optional[str] = Field(default=None, description="配置描述")
    enabled: bool = Field(default=True, description="是否启用")
    metrics: List[SemanticMetric] = Field(default_factory=list, description="指标列表")
    dimensions: List[SemanticDimension] = Field(default_factory=list, description="维度列表")
    relationships: List[SemanticRelationship] = Field(default_factory=list, description="表关系列表")


class SemanticConfigUpdateRequest(BaseModel):
    """更新语义配置请求"""
    name: Optional[str] = Field(default=None, description="配置名称")
    description: Optional[str] = Field(default=None, description="配置描述")
    enabled: Optional[bool] = Field(default=None, description="是否启用")
    metrics: Optional[List[SemanticMetric]] = Field(default=None, description="指标列表")
    dimensions: Optional[List[SemanticDimension]] = Field(default=None, description="维度列表")
    relationships: Optional[List[SemanticRelationship]] = Field(default=None, description="表关系列表")


class SemanticAutoPrefillRequest(BaseModel):
    """语义字段自动预填请求"""
    selected_tables: List[str] = Field(default_factory=list, description="当前数据集已选择的数据表")


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
    description: Optional[str] = Field(default=None, description="表描述")
    columns: List[SemanticPrefillColumn] = Field(default_factory=list, description="字段列表")


class SemanticPrefillRequest(BaseModel):
    """语义字段预填请求（短路由版）"""
    datasource_id: str = Field(..., description="数据源 ID")
    config_id: str = Field(..., description="语义配置 ID")
    tables: List[SemanticPrefillTable] = Field(default_factory=list, description="当前待预填的数据表列表")


# ============ 树形过滤条件模型（参考 Cube.js 设计）============

from typing import Union, ForwardRef

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

class ExecuteSemanticInput(BaseModel):
    """语义查询执行工具的输入参数（供 LangGraph Agent 工具使用）"""
    model_config = {"extra": "ignore"}
    metrics: List[str] = Field(
        description="要输出的字段名称列表。聚合查询时填写可用指标；明细查询时也可填写可用维度/原始字段"
    )
    dimensions: List[Union[str, Dict[str, str]]] = Field(
        default=[],
        description='''维度列表，支持两种格式：
1. 简单格式: ["维度名1", "维度名2"]
2. 带时间粒度: [{"name": "时间维度名", "time_grain": "month"}]
   time_grain 可选值: year, quarter, month, week, day, hour'''
    )
    filters: List[Dict] = Field(
        default=[],
        description='''WHERE 过滤条件，支持两种格式：
1. 平铺格式（简单场景）: [{"dimension": "维度名", "operator": "=", "value": "值"}]
2. 树形格式（支持OR逻辑）: [{"or": [条件1, 条件2, ...]}] 或 [{"and": [条件1, 条件2, ...]}]'''
    )
    having: List[Dict] = Field(
        default=[],
        description='HAVING 过滤条件列表（过滤聚合结果），格式: [{"metric": "指标名", "operator": ">", "value": 10}]'
    )
    order_by: List[Dict] = Field(
        default=[],
        description='排序列表，格式: [{"field": "指标名或维度名", "direction": "ASC或DESC"}]'
    )
    limit: Optional[int] = Field(
        default=None,
        description='限制返回条数。当用户说"只显示N个"、"前N个"、"TOP N"时使用'
    )
    compare: Optional[Dict[str, str]] = Field(
        default=None,
        description='''同比/环比配置（可选），格式:
{
  "type": "mom" | "yoy",
  "mode": "trend" | "value",
  "strategy": "calc" | "metric" | "post_calc"
}
说明：
- type: mom=环比, yoy=同比
- mode: trend=趋势序列, value=单值
- strategy: calc=运行时SQL计算, metric=查预计算字段, post_calc=结果层再算
当前仅支持 strategy=calc'''
    )
    visualization: Optional[Dict] = Field(
        default=None,
        description='''图表推荐配置，根据查询意图判断最适合的展示方式。格式:
{
  "type": "table" | "card" | "bar" | "line" | "pie",
  "x_field": "X轴列名（必须与SQL返回的列名一致）",
  "y_field": "指标名（Y轴）"
}
x_field 命名规则（重要）:
- 普通维度: 直接用维度名，如 "数据库类型"
- 时间维度(有time_grain): 用 "维度名_时间粒度"，如 "创建时间_month"、"下单时间_quarter"
推荐规则：
- 无明确聚合统计需求 → "table"
- 单值聚合（无维度）→ "card"
- 有时间维度 → "line"
- 分类维度 ≤6 个且单指标 → "pie"
- 其他 → "bar"'''
    )

    @model_validator(mode="before")
    @classmethod
    def reject_legacy_mode(cls, data: Any):
        """显式拒绝已废弃的 mode 参数，同时允许工具运行时注入的内部字段。"""
        if isinstance(data, dict) and "mode" in data:
            raise ValueError("参数 mode 已废弃，请移除 mode 字段")
        return data


class ValidateSQLInput(BaseModel):
    """验证SQL工具的输入参数"""
    sql: str = Field(..., description="要验证的SQL语句")
    datasource_id: Optional[str] = Field(default=None, description="数据源ID，未传时默认使用当前会话数据源")


# ============ Agent 工具输入模型 ============

class ExecuteSqlInput(BaseModel):
    """执行 SQL 工具的输入参数"""
    sql: str = Field(..., description="要执行的 SQL 语句")


class GenerateChartInput(BaseModel):
    """图表生成工具的输入参数"""
    result_id: str = Field(
        description="查询结果ID，从 execute_sql_tool 返回的 result_id"
    )
    chart_type: Optional[Literal["bar", "line", "pie"]] = Field(
        default=None,
        description="图表类型。不传则返回数据分析，由你来推荐"
    )
    x_field: Optional[str] = Field(
        default=None,
        description="X轴字段名。不传则返回数据分析"
    )
    y_field: Optional[str] = Field(
        default=None,
        description="Y轴字段名（必须是数值类型）。不传则返回数据分析"
    )
    series_field: Optional[str] = Field(
        default=None,
        description="分组字段名（可选），传入则生成多折线图"
    )


class LoadSkillInput(BaseModel):
    """加载 Skill 工具的输入参数"""
    skill_name: str = Field(
        description="要加载的 skill 名称，如 chart-processing"
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
    chart_type: Literal["table", "bar", "horizontal_bar", "line", "pie", "map", "kpi"] = Field(
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
    chart_type: Literal["table", "bar", "horizontal_bar", "line", "pie", "map", "kpi"] = Field(
        default="table",
        description="图表类型"
    )
    name: Optional[str] = Field(default=None, description="用户显式传入的名称，存在时优先使用")


class PanelUpdateRequest(BaseModel):
    """更新面板请求"""
    name: Optional[str] = Field(default=None, description="面板名称")
    sql: Optional[str] = Field(default=None, description="SQL查询语句")
    chart_type: Optional[Literal["table", "bar", "horizontal_bar", "line", "pie", "map", "kpi"]] = Field(
        default=None,
        description="图表类型"
    )
    chart_config: Optional[Dict] = Field(default=None, description="图表配置")
    layout_x: Optional[int] = Field(default=None, description="布局X位置")
    layout_y: Optional[int] = Field(default=None, description="布局Y位置")
    layout_w: Optional[int] = Field(default=None, description="布局宽度")
    layout_h: Optional[int] = Field(default=None, description="布局高度")
    refresh_interval: Optional[int] = Field(default=None, description="刷新间隔(秒)")


class LayoutUpdateRequest(BaseModel):
    """批量更新布局请求"""
    layouts: List[Dict] = Field(
        ...,
        description="布局列表 [{'panel_id': 'xxx', 'x': 0, 'y': 0, 'w': 6, 'h': 4}, ...]"
    )
