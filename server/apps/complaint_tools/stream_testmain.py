import asyncio
from .case_analysis_tool_identify_v2 import (
    stream_identify_case_items,
)
from .case_analysis_tool_analyze_v2 import (
    stream_analyze_case_items,
)

async def tool1_test():
    gen = stream_identify_case_items(
        user_id="user-123",
        base_id="base-456",
        content=(
            "2023年5月，我通过我行手机银行向外行转账5万元，界面提示“交易失败”，"
            "但对方账户实际收到款项，且投诉人账户流水显示扣款成功。投诉人认为系统提示存在误导，"
            "要求退还手续费并说明原因。"
        ),
    )

    async for chunk in gen:
        # 每个 chunk 是一次 SSE 事件序列化后的字符串
        print("=== SSE CHUNK ===")
        print(chunk)

async def tool2_test():
    gen = stream_analyze_case_items(
        user_id="user-123",
        base_id="base-456",
        content=(
            "2023年5月，我通过我行手机银行向外行转账5万元，界面提示“交易失败”，"
            "但对方账户实际收到款项，且投诉人账户流水显示扣款成功。投诉人认为系统提示存在误导，"
            "要求退还手续费并说明原因。"
        )
    )

    async for chunk in gen:
        # 每个 chunk 是一次 SSE 事件序列化后的字符串
        print("=== SSE CHUNK ===")
        print(chunk)



if __name__ == "__main__":
    asyncio.run(tool2_test())
