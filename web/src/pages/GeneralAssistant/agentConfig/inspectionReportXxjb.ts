/**
 * 信息简表撰写 - 智能体配置
 */

import type { Agent } from "../types";
import { InspectionEmbedded } from "./components";

export const inspectionReportXxjb: Agent = {
  id: "inspection-report-xxjb",
  name: "信息简表撰写",
  description: "快速撰写检查信息简表",
  category: "inspection",
  icon: "fa-table",
  color: "purple",
  displayMode: "inline",
  navigatePath: "/report_xxjb",
  guide: {
    title: "信息简表撰写",
    description: "上传检查报告，快速生成信息简表",
    icon: "fa-table",
    iconColor: "purple",
  },
  component: InspectionEmbedded,
  componentProps: {
    agentId: "report_xxjb",
  },
};
