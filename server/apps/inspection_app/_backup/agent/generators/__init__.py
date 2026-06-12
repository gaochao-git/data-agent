"""
报告生成器模块
每个生成器负责特定报告类型的文档生成逻辑

所有生成器统一使用 violations_analysis_result 作为数据源
"""

from .jcbg_generator import generate_jcbg_document, extract_jcbg_data
from .zgtzs_generator import generate_zgtzs_document, extract_zgtzs_data
from .xxjb_generator import generate_xxjb_document, extract_xxjb_data
from .zjml_generator import generate_zjml_document, extract_zjml_data

__all__ = [
    "generate_jcbg_document",
    "extract_jcbg_data",
    "generate_zgtzs_document",
    "extract_zgtzs_data",
    "generate_xxjb_document",
    "extract_xxjb_data",
    "generate_zjml_document",
    "extract_zjml_data",
]
