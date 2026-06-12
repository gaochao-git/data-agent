/**
 * 证据目录撰写 - 智能体配置
 */

import type { Agent } from "../types";
import { InspectionEmbedded } from "./components";

export const inspectionReportZjml: Agent = {
  id: "inspection-report-zjml",
  name: "证据目录撰写",
  description: "自动撰写检查材料目录",
  category: "inspection",
  icon: "fa-list",
  color: "blue",
  displayMode: "inline",
  navigatePath: "/report_zjml",
  guide: {
    title: "证据目录撰写",
    description: "上传检查报告，自动生成证据材料目录",
    icon: "fa-list",
    iconColor: "blue",
  },
  component: InspectionEmbedded,
  componentProps: {
    agentId: "report_zjml",
  },
};
