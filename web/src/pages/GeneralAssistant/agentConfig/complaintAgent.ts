/**
 * 举报件办理 - 智能体配置
 */

import type { Agent } from "../types";
import { UnifiedEmbedded } from "./components";

export const complaintAgent: Agent = {
  id: "complaint_agent",
  name: "举报件办理智能体",
  description: "智能辅助举报件办理流程",
  category: "complaint",
  icon: "fa-clipboard-check",
  color: "orange",
  displayMode: "inline",
  navigatePath: "/complaint",
  opening_message: {
    title: "举报件办理",
    icon: "fa-clipboard-check",
    content: "举报办理智能体依托AI技术，实现举报材料的智能化辅助处理。\n<b>- 视觉理解</b>：运用视觉大模型技术，实现对举报扫描件中的文字、图片、图标信息的综合理解。\n<b>- 智能摘要</b>：运用推理大模型技术，快速提炼举报材料中的核心要素与诉求，生成清晰的摘要信息，助力“秒懂”要点。\n<b>- 辅助分析</b>：运用支付结算举报办理专家经验，研判举报事项归属、关联法律法规，为举报办理人员提供支撑。",
    actions: [
      {
        id: "upload",
        label: "上传文件",
        description: "上传举报件，AI 自动识别内容",
        kind: "upload",
      },
    ],
  },
  input_actions: [
    { id: "deepseek-r1", label: "DeepSeek-R1", kind: "model", defaultSelected: true },
    { id: "deep-think", label: "深度思考", kind: "toggle", icon: "deep-think", defaultSelected: true },
    // { id: "complaint-analysis", label: "举报项分析", kind: "custom", defaultSelected: false },
  ],
  input_controls: {
    upload: {
      enabled: true,
      accept: "application/pdf,image/*,.pdf,.png,.jpg,.jpeg,.bmp,.gif,.webp,.heic,.heif,.tiff",
      maxCount: 5,
    },
  },
  input_placeholder: "帮我分析举报件，附件是",
  footer_tag: "举报件办理",
  assistant_role_label: "AI",
  mock_messages: [
    {
      message_id: "c-1",
      role: "user",
      text: "帮我分析举报件，附件是****.png",
      attachment: {
        id: "complaint_001:file_20251210",
        name: "2025-12-10.png",
      },
      time: "17:54:20",
    },
    {
      message_id: "c-2",
      role: "assistant",
      time: "17:54:30",
      steps: [
        {
          id: "thinking",
          label: "思考已完成",
          status: "done",
          detail:
            "法人需要注意的3项关键事项总结如下：\n1、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
        },
        {
          id: "tool",
          label: "工具调用已完成",
          status: "done",
          children: [
            {
              id: "thinking-sub-1",
              label: "规划完成",
              status: "done",
              detail:
                "法人需要注意的3项关键事项总结如下：\n1、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
            },
            {
              id: "thinking-sub-2",
              label: "信息抽取完成",
              status: "done",
              detail:
                "法人需要注意的3项关键事项总结如下：\n2、打算看很费劲哪思考打发时间的看法哈开始给他风华卡就很少有风景中来电话费卡健身房看见",
            },
          ],
        },
        { id: "summary", label: "结论生成中", status: "thinking", detail: "生成中..." },
      ],
      content:
        "打算看举报邮件的时间与内容开始始终化代风华卡航班很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡很少有风景中来电话费卡。",
      suggestions: [
        { tool_id: "complaint_analysis", name: "分析举报事项" },
        { tool_id: "export_flow", name: "导出流水" },
      ],
    },
  ],
  component: UnifiedEmbedded,
  componentProps: {
    agentId: "complaint_agent",
    toolType: "handling",
  },
  workbench: {
    enabled: true,
    variant: "complaint",
  },
};
