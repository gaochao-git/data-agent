/**
 * 事实认定分析 - 智能体配置
 */

import type { Agent } from "../types";
import { InspectionEmbedded } from "./components";

export const inspectionFactAnalysis: Agent = {
  id: "inspection-fact-analysis",
  name: "事实认定分析",
  description: "辅助分析执法检查事实认定",
  category: "inspection",
  icon: "fa-scale-balanced",
  color: "blue",
  displayMode: "inline",
  navigatePath: "/fact_analysis",
  guide: {
    title: "事实认定分析",
    description: "智能分析事实认定内容，自动匹配违反条款、处罚依据和整改建议",
    icon: "fa-scale-balanced",
    iconColor: "blue",
    examplePrompts: [
      "分析这段事实认定的违反条款",
      "这个行为应该如何处罚",
    ],
  },
  component: InspectionEmbedded,
  componentProps: {
    agentId: "fact_analysis",
  },
};
