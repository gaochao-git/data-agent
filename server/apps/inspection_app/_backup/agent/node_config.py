"""
节点配置文件
定义各个报告子图的任务列表和特定配置

配置字段说明：
- report_type: 报告类型标识（用于路由生成器）
- report_name: 报告中文名称
- tasks: 任务列表（用于前端进度显示）
- has_extract_step: 是否有额外数据提取步骤
- has_evidence_review: 是否有证据审核步骤
- extra_check: 额外依赖检查函数（在运行时注入）
"""

# ========== 统一任务 ID 定义 ==========
# 所有报告使用相同的任务 ID 命名规范

TASK_IDS = {
    "check": "check_deps",       # 检查依赖
    "extract_doc": "extract_doc", # 解析文档
    "review_extract": "review_extract",  # 确认解析
    # "analyze": "analyze",         # 已删除：分析违法行为
    # "review_analyze": "review_analyze",  # 已删除：确认分析
    "retrieve": "retrieve",       # 检索法律依据
    "review_retrieve": "review_retrieve", # 确认检索
    "extract": "extract",         # 额外数据提取（可选）
    "review_evidence": "review_evidence", # 证据审核（可选）
    "generate": "generate",       # 生成文档
}

# ========== 公共任务列表（基础流程）==========

TASKS_BASE = [
    {"id": "check_deps", "title": "检查所需文件"},
    {"id": "extract_doc", "title": "解析事实认定书"},
    {"id": "review_extract", "title": "确认解析结果"},
    # {"id": "analyze", "title": "分析违法行为"},  # 已删除：在检索时自动创建
    # {"id": "review_analyze", "title": "确认分析结果"},  # 已删除：简化流程
    {"id": "retrieve", "title": "检索法律依据"},
    {"id": "review_retrieve", "title": "确认检索结果"},
]

# 从检查报告提取的任务列表（用于其他报告）
TASKS_FROM_REPORT = [
    {"id": "check_deps", "title": "检查所需文件"},
    {"id": "extract_from_report", "title": "从检查报告提取数据"},
    {"id": "review_retrieve", "title": "确认提取结果"},
]

# ========== 各报告任务列表 ==========

TASKS_JCBG = TASKS_BASE + [
    {"id": "generate", "title": "生成Word文档"}
]

# 整改通知书：从检查报告提取
TASKS_ZGTZS = TASKS_FROM_REPORT + [
    {"id": "generate", "title": "生成Word文档"}
]

# 信息简表：从检查报告提取（数据转换在生成步骤中完成）
TASKS_XXJB = TASKS_FROM_REPORT + [
    {"id": "generate", "title": "生成Excel文档"}
]

# 证据目录：从检查报告提取（取证记录表在确认提取结果阶段上传）
TASKS_ZJML = TASKS_FROM_REPORT + [
    {"id": "generate", "title": "生成Excel文档"}
]

# ========== 报告配置 ==========

CONFIG_JCBG = {
    "report_type": "jcbg",
    "report_name": "检查报告",
    "tasks": TASKS_JCBG,
    "input_type": "fact_determination",  # 输入：事实认定书
    "has_extract_step": False,
    "has_evidence_review": False,
    "has_extract_from_report": False,  # 不从检查报告提取
    "extra_check": None,
}

CONFIG_ZGTZS = {
    "report_type": "zgtzs",
    "report_name": "整改通知书",
    "tasks": TASKS_ZGTZS,
    "input_type": "inspection_report",  # 输入：检查报告
    "has_extract_step": False,
    "has_evidence_review": False,
    "has_extract_from_report": True,  # 从检查报告提取
    "extra_check": None,
}

CONFIG_XXJB = {
    "report_type": "xxjb",
    "report_name": "信息简表",
    "tasks": TASKS_XXJB,
    "input_type": "inspection_report",  # 输入：检查报告
    "has_extract_step": False,  # 不需要额外的extract节点（在generate中完成数据转换）
    "has_evidence_review": False,
    "has_extract_from_report": True,  # 从检查报告提取
    "extra_check": None,
}

CONFIG_ZJML = {
    "report_type": "zjml",
    "report_name": "证据目录",
    "tasks": TASKS_ZJML,
    "input_type": "inspection_report",  # 输入：检查报告
    "has_extract_step": False,  # 不需要额外的extract节点
    "has_evidence_review": False,  # 不需要单独的证据审核节点（在review_retrieve阶段上传取证记录表）
    "has_extract_from_report": True,  # 从检查报告提取
    "extra_check": None,
}

# ========== 报告类型映射（便于查找）==========

REPORT_CONFIGS = {
    "jcbg": CONFIG_JCBG,
    "zgtzs": CONFIG_ZGTZS,
    "xxjb": CONFIG_XXJB,
    "zjml": CONFIG_ZJML,
}

# ========== 报告类型中文名映射 ==========

REPORT_TYPE_NAMES = {
    "jcbg": "检查报告",
    "zgtzs": "整改通知书",
    "xxjb": "信息简表",
    "zjml": "证据目录",
}
