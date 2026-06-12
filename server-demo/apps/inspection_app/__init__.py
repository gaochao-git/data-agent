"""
执法检查应用模块

包含5个独立智能体：
- fact_analysis: 事实认定分析
- report_jcbg: 检查报告撰写
- report_zgtzs: 整改通知书撰写
- report_zjml: 证据目录撰写
- report_xxjb: 信息简表撰写
"""

from .routes import router as violation_router
from utils.storage_paths import VIOLATION_BASE_DIR

# 允许上传的文件类型
ALLOWED_EXTENSIONS = ['.docx', '.wps']

# 导入并注册5个智能体（导入时自动注册）
from . import fact_analysis_agent
from . import report_jcbg_agent
from . import report_zgtzs_agent
from . import report_zjml_agent
from . import report_xxjb_agent

# 导出所有 agent_id
AGENT_IDS = [
    fact_analysis_agent.AGENT_ID,
    report_jcbg_agent.AGENT_ID,
    report_zgtzs_agent.AGENT_ID,
    report_zjml_agent.AGENT_ID,
    report_xxjb_agent.AGENT_ID,
]

__all__ = [
    'violation_router',
    'AGENT_IDS',
    'ALLOWED_EXTENSIONS',
]
