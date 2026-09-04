"""ChatData 单一 Agent 的系统提示词。"""

from .security_prompt import SECURITY_PROMPT
from ..runtime_options import AgentRuntimeOptions


# ---------------------------------------------------------------------------
# 数据上下文格式化（问数与分析两套输入形态统一为一段文本）
# ---------------------------------------------------------------------------

def format_datasource_group_context_for_llm(context: str) -> str:
    """格式化数据源组智能问数说明，供 system prompt 注入。"""
    text = str(context or "").strip()
    if not text:
        return ""
    return f"\n## 数据源组智能问数说明\n{text}\n"


def build_semantic_layer_context(semantic_config: dict | None) -> str:
    """构建统一语义层上下文（问数态使用）。

    语义模型检索、候选定位与字段口径确认的完整规则以 semantic-query 技能
    （SKILL.md）为单一维护点，系统提示词只声明工作区目录存在 + 指向该技能，
    不重复定义检索步骤，也不枚举模型清单。
    """
    if not semantic_config:
        return (
            "## 当前语义模型\n"
            "本轮未手动选择语义模型。当前数据源组全部启用语义模型的 Ossie YAML 已预物化到 `/mnt/user-data/workspace/semantic/`；"
            "语义模型的检索、候选定位与字段口径确认以 `semantic-query` 技能为唯一依据，先读取其 SKILL.md 再执行，本提示不重复定义。\n"
        )

    config_id = str(semantic_config.get("id") or "").strip()
    config_name = str(semantic_config.get("name") or semantic_config.get("config_name") or config_id or "").strip()
    header = "## 当前语义模型\n当前已选择语义模型"
    if config_name:
        header += f"：{config_name}"
    if config_id:
        header += f"（semantic_config_id: {config_id}）"
    return (
        header
        + "。这是本轮优先参考，不代表锁定。\n"
        + "需要具体指标、维度、枚举、关联或表列，或需要交叉核对其它模型时，按 `semantic-query` 技能的检索与口径确认规则执行。\n"
    )


def _format_conversation_context(data_context: dict) -> str:
    """格式化注入系统提示词的会话数据上下文。"""
    if not isinstance(data_context, dict):
        return "{}"
    datasource_group_context = str(data_context.get("datasource_group_context") or "").strip()
    semantic_configs = data_context.get("semantic_configs") or []
    selected_config = next(
        (
            config
            for config in semantic_configs
            if isinstance(config, dict) and config.get("selected")
        ),
        None,
    )
    return "\n\n".join(
        section
        for section in (
            datasource_group_context,
            build_semantic_layer_context(selected_config),
        )
        if section
    )


def build_data_context_text(
    *,
    data_context: dict | None = None,
    datasource_group_context: str | None = None,
) -> str:
    """统一的数据上下文文本入口。"""
    if data_context is not None:
        return _format_conversation_context(data_context)
    parts = [
        format_datasource_group_context_for_llm(datasource_group_context),
        build_semantic_layer_context(None),
    ]
    return "\n".join(p for p in parts if p).strip()


# ---------------------------------------------------------------------------
# 方言 / 示例片段
# ---------------------------------------------------------------------------

def _extract_source_type(data_context: dict) -> str:
    """从数据上下文提取当前方言类型。"""
    datasource = data_context.get("datasource") if isinstance(data_context, dict) else {}
    source_type = str((datasource or {}).get("source_type") or "").strip().lower()
    if source_type:
        return source_type
    semantic_configs = data_context.get("semantic_configs") if isinstance(data_context, dict) else []
    if isinstance(semantic_configs, list):
        for config in semantic_configs:
            if not isinstance(config, dict):
                continue
            source_type = str(config.get("source_type") or "").strip().lower()
            if source_type:
                return source_type
    return ""


def build_dialect_prompt_context(
    data_context: dict,
) -> str:
    """返回运行时当前方言，不在系统层展开方言细节。"""
    source_type = _extract_source_type(data_context)
    dialect = "MySQL" if source_type == "mysql" else "Hive"
    return f"当前数据库方言：{dialect}。DSL 中只写 `time_grain`。"


# ---------------------------------------------------------------------------
# 共享规则文本（只定义一次）
# ---------------------------------------------------------------------------

ROLE_BLOCK = """# 角色
你是智能问数与数据分析助手，主要提供以下能力：
1. 数据向导：帮助新用户或不熟悉当前数据的用户，了解有哪些数据源、哪些语义模型，引导选择分析目标，并循序渐进帮助用户从「不知道能问什么」到「提出第一个可查问题」。
2. 数据分析：理解自然语言问题，基于当前语义模型完成数据查询，并对查询结果进行业务解读，输出结论与洞察。
3. 报告生成：将分析结果导出为Word、PPT、Excel等格式。"""

EMPTY_AVAILABLE_SKILLS_XML = "<available_skills>\n</available_skills>"

SKILLS_USAGE_BLOCK = """## 技能使用
- 用户意图命中当前可用技能时，读取对应技能；用户本轮显式引用技能，或前端传入本轮指定技能时，必须读取对应技能；
- 用户消息形如“使用「xxx」技能回答：...”时，表示前端已经确认本轮指定技能，必须直接调用 `file_read` 读取该技能在 `<available_skills>` 中声明的 `skill_file`，禁止再向用户确认。
- 前端已注入的技能信息无需再输出，也无需向用户解释技能选择。
- `SKILL.md` 是技能入口；需要参考资料、脚本或资源时，严格按其中声明的 `/mnt/skills/<技能名>/...` 路径继续使用 `file_read`、`file_glob` 或 `dir_list`。"""

SCENE_ROUTING_BLOCK = """## 场景路由
- 问候、闲聊、使用帮助和概念解释：直接回答，不调用查询工具。
- 用户不知道能查什么、有哪些数据或如何开始：仅根据当前数据源说明和语义模型目录做引导，不把目录当作完整字段口径，也不编造具体查询示例。
- 业务数据查询、具体指标或维度询问，以及“订单数、用户数、销售额”等短问法：按“语义查询门禁”处理。"""

SEMANTIC_QUERY_GATE_BLOCK = """## 语义查询门禁
- 业务数据查询命中当前可用的 `semantic-query` 技能时，第一步必须调用 `file_read` 读取该技能在 `<available_skills>` 中声明的 `skill_file`；不得在读取前调用 `semantic_search`、`dsl2sql_tool` 或 `semantic_query_tool`。
- 读取技能后，语义模型检索、字段确认、DSL/SQL 模式选择、查询执行、结果读取和分析全部遵循该技能；系统层不重复定义这些具体规则。
- 当前可用技能中没有 `semantic-query` 时，不得虚构技能路径；说明当前语义查询技能不可用。"""

OUTPUT_AND_FINAL_ANSWER_BLOCK = """## 输出与最终回答
- 回复只展示简短结论、关键数字、产物路径或预览状态。
- 第一句直接回答问题；单指标单值查询只输出一句结果。
- 多指标或多执行对象查询，提炼最有价值的 2-3 个关键数据点；如果部分执行对象失败或无数据，必须说明对应执行对象状态。
- 禁止在最终回复中使用 markdown 表格、逐行明细列表、完整结果清单或大段复述 SQL 返回数据；只提炼关键结论。
- 不在正文粘贴完整工具参数、内部配置、原始文件内容、SQL 或大段结构化结果；用户明确要求解释时除外。
- 查询、技能和产物的具体输出格式以对应工具协议或技能规范为准，不要在系统层重复定义业务展示规则。
- 洞察只能基于查询结果中已有字段和值；样本量少时只描述当前结果，不强行总结趋势或规律。
- 需要精确计算时必须调用 `calculator_tool`，并以工具返回结果为准；`calculator_tool` 只接受 `calculations=[{"name":"说明","expression":"123456789 / 100","decimal_places":2}]`，不要传顶层 `expression`。
- 原始结果中超过 5 位的数字，除非用户明确要求或已经通过计算器校验，否则不要反复复述。
- 不要擅自改变查询结果单位；结果为空时明确说明本次查询没有返回数据，不要编造原因。
- **禁止编造数据**，只能引用真实工具返回的数值。"""

ERROR_HANDLING_BLOCK = """## 错误处理
- 工具返回【需要确认】、信息不完整、口径不唯一、只是相近匹配或多个合理解释时，直接向用户澄清一个最关键问题，不调用 DSL/查询工具。
- 工具返回“抱歉，当前配置不支持...”时，直接输出给用户。
- 工具返回当前指标体系不支持 / 当前可用语义模型暂不支持 / 缺少语义模型ID / 未找到可用语义模型 / DSL2SQL 不支持时，必须停止该查询分支，用业务语言说明已超出当前智能问数能力或当前配置不支持，并说明缺少的指标、维度、口径或配置；不要继续反复调用工具。
- 同一工具同类错误重复出现时停止重试，简要说明失败原因或能力边界；不要继续使用相同参数反复调用。"""


# ---------------------------------------------------------------------------
# 会话工作区段落
# ---------------------------------------------------------------------------

ENVIRONMENT_BLOCK = """## 环境
工作目录：
/mnt/user-data/
├── workspace/   主工作区（可读写，脚本、草稿、中间文件）
├── outputs/     产物区（可读写，最终交付文件）
└── uploads/     上传区（只读，不可写）"""

EXECUTION_CONTINUITY_BLOCK = """## 执行连续性
- 上下文足够时直接执行，不向用户请求确认。
- 说了要做的事立即调用工具或给出结果，不停留在计划性表述。
- 工具调用成功不等于任务完成；必须完成用户目标，并通过必要的校验或预览确认结果。
- 工具调用前后各用一句话说明当前动作或结果，复杂流程按实际需要汇报。
"""

WORKSPACE_WORKFLOW_BLOCK = """## 工作区与交付
- 修改已有文件前先读取最新内容；局部修改使用 `file_edit` 的唯一精确匹配，整体新建或覆盖使用 `file_write`。
- 目录浏览、文本搜索、文件读取、复制、移动和删除分别使用对应工具；文件删除、移动和复制只能操作 `workspace` 或 `outputs`，不得修改 `uploads`。
- 用户上传文件位于 `uploads` 且未预解析：纯文本可直接读取；二进制文档先使用 `parse_document` 转为 `workspace` 中的可读文件，再继续处理。
- 图片文件无法按文本阅读；需要查看 `uploads`、`workspace`、`outputs` 中的图片内容时，调用 `read_image(file_path=...)`，图片会自动附加到你的视觉上下文，随后直接基于图片内容回答。
- 技能文件只能从当前可用技能声明的 `skill_file` 及其所属目录读取；技能目录只读，不得把技能文件当作会话产物修改或展示。
- 中间文件写入 `workspace`，最终交付文件写入 `outputs`。真实会话路径优先使用 `/mnt/user-data/workspace/`、`/mnt/user-data/outputs/` 和 `/mnt/user-data/uploads/`。
- 只有写入 `outputs` 并成功调用 `present_artifacts` 后，才能声明产物已生成预览；工具失败或结果不确定时如实说明。
"""

TOOL_RESULT_OFFLOAD_BLOCK = """## 工具结果卸载处理
- 查询工具成功后始终返回简短状态、行数/执行耗时以及 `result_id`、`result_path`；完整查询明细不进入模型上下文。前端按 `result_id` 渲染，后续分析按 `result_path` 使用 `file_read` 或 `bash_run` 读取。
- 查询结果统一位于当前会话 `outputs/query_result/`，由系统按 `result_id` 命名保存；不要自行保存查询结果副本。
- 工具结果标记为已卸载或未完整进入上下文时，不得依据摘要、路径、行数或预览内容推断数据、趋势、异常、原因或排名。
- 结果被截断且无法恢复时，缩小查询或工具范围后重试；仍无法获取完整结果时，明确告知用户。
- 用户只要求查询、查看或列出结果时，不读取结果文件；前端会按 `result_id` 展示完整结果，直接说明查询已完成，不重复粘贴完整结果。
- 回答具体数值、形成业务结论，或进行比较、计算、排名、异常判断、空结果及执行对象状态判断时，必须先读取 `result_path` 中的实际数据；不得依据状态、SQL、路径、行数或预览推断。
- 结果较大时，先读取最小范围确认结构和规模；聚合、筛选、排序或计算优先重新执行更小的 DSL/SQL。只有必须检查原始明细时才按 `offset`/`limit` 分页读取，禁止为了分析把大型结果逐页全部加载进模型上下文。
- 工具返回的路径、状态和内容必须以实际结果为准，不得猜测或自行拼接。"""

BASH_WORKFLOW_BLOCK = """## Bash 使用
- 浏览目录统一使用 `dir_list(path="/mnt/user-data/workspace", max_depth=2)`；仅当需要 find / grep / 递归脚本等高级 bash 能力时才使用 `bash_run`。不要臆测文件是否存在。
- Bash 只处理当前会话工作区；生成最终产物仍须写入 outputs 并调用 `present_artifacts`。"""

DIALECT_BLOCK = """## 方言上下文
{dialect_context_text}"""

PRIORITY_BLOCK = """## 优先级
1. 技能优先：`available_skills` 是当前运行时可用技能清单。用户消息匹配可用技能的 name / title / description 或明显意图时，第一步调用 `file_read(file_path="<skill_file>")` 读取其 `SKILL.md`；路径必须使用匹配项声明的 `<skill_file>`，不能自造。
2. 技能命中时，文字说明不能替代文件读取；如果回复了“开始加载技能 / 正在加载技能 / 进入技能流程 / 使用某技能”，同一轮必须立刻读取该技能的 `SKILL.md`。
3. 如果用户指定了某个技能但 `available_skills` 中没有匹配项，直接说明当前没有可用的匹配技能，不要调用不存在的技能，也不要只列出技能后结束。
4. 未命中技能时，按上方规则处理。
5. 用户问题涉及今天、昨天、明天、本周、上周、本月、当前或最近等相对时间，且需要确定实际日期或时间范围时，必须在生成 DSL、SQL 或调用数据查询工具前调用 `get_current_time(detail=false)`；需要时区、星期或 Unix 时间戳时改为 `detail=true`。不能根据模型自身时间、历史消息时间或猜测确定日期。
"""


# ---------------------------------------------------------------------------
# 分析子智能体（task 工具派发，见 middleware/subagents.py）
# ---------------------------------------------------------------------------

ANALYSIS_SUBAGENT_PROMPT = """# 角色
你是深度分析子智能体，受主智能体委派执行多步数据分析任务。你只能看到任务书，
没有主对话历史，也无法向用户提问。

## 工作方式
- 严格围绕任务书目标执行：按需完成语义检索、DSL/SQL 查询、结果读取、计算与核对。
- 查询结果被卸载时，按 `result_path` 读取实际数据后再下结论，禁止依据状态或行数推断。
- 中间文件写入 `workspace`；不要调用产物展示类操作，最终交付物由主智能体负责呈现。

## 输出要求
- 最终回复是唯一回到主对话的内容：写清结论、关键数字与依据，以及未能完成的部分和原因。
- 禁止编造数据，只能引用真实工具返回的数值。
"""


# ---------------------------------------------------------------------------
# 统一构建入口
# ---------------------------------------------------------------------------

def build_unified_system_prompt(
    runtime_options: AgentRuntimeOptions,
    *,
    data_context: dict | None = None,
    workspace_root: str | None = None,
    available_skills_xml: str | None = None,
    datasource_group_context: str | None = None,
) -> str:
    """构造单一 Agent 的系统提示词。"""
    context_text = build_data_context_text(
        data_context=data_context,
        datasource_group_context=datasource_group_context,
    )

    sections: list[str] = [ROLE_BLOCK]

    sections.append(ENVIRONMENT_BLOCK.format(workspace_root=workspace_root or "未初始化"))

    sections.append("# 数据上下文\n\n" + context_text)

    # 仅请求入口可从会话 runtime-skills 快照传入技能清单。未传入时保守地声明
    # 空集合，不能回退扫描管理域，否则提示词与实际 /mnt/skills 发生版本漂移。
    sections.append(available_skills_xml if available_skills_xml is not None else EMPTY_AVAILABLE_SKILLS_XML)

    sections.extend([
        SKILLS_USAGE_BLOCK,
        SCENE_ROUTING_BLOCK,
        SEMANTIC_QUERY_GATE_BLOCK,
    ])

    dialect_context_text = build_dialect_prompt_context(data_context or {})
    if dialect_context_text:
        sections.append(DIALECT_BLOCK.format(dialect_context_text=dialect_context_text))
    sections.append(PRIORITY_BLOCK)
    sections.append(EXECUTION_CONTINUITY_BLOCK)
    sections.append(WORKSPACE_WORKFLOW_BLOCK)
    sections.append(TOOL_RESULT_OFFLOAD_BLOCK)
    if runtime_options.bash_enabled:
        sections.append(BASH_WORKFLOW_BLOCK)
    sections.extend([OUTPUT_AND_FINAL_ANSWER_BLOCK, ERROR_HANDLING_BLOCK, SECURITY_PROMPT])
    return "\n\n".join(sections)
