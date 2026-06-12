import json

from langchain.tools import tool, ToolRuntime


@tool
def get_legal(query: str, runtime: ToolRuntime) -> str:
    """帮助进行法律条文、监管政策、历史案例等检索与初步分析。"""
    writer = runtime.stream_writer
    writer(f"开始检索与「{query}」相关的法规与条款")
    mock_result = {
        "query": query,
        "statutes": [
            {
                "law": "中华人民共和国民法典",
                "article": "第一百二十条",
                "summary": "规定了侵害他人民事权益应承担的民事责任，包括停止侵害、赔偿损失等。",
                "penalty_range": "民事赔偿，具体金额依据损失评估确定",
                "reference": "https://example.com/civil-code-120",
            },
            {
                "law": "中华人民共和国消费者权益保护法",
                "article": "第八条",
                "summary": "消费者享有知悉其购买、使用的商品或者接受的服务的真实情况的权利。",
                "penalty_range": "责令改正、没收违法所得、罚款；情节严重的可责令停业整顿",
                "reference": "https://example.com/consumer-protection-8",
            },
        ],
        "notes": "以上为示例数据，需根据实际检索结果替换。",
    }
    writer("完成初步匹配，返回示例法规条目")
    return json.dumps(mock_result, ensure_ascii=False, indent=2)


__all__ = ["get_legal"]
