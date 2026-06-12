/**
 * 智能问数 - 智能体配置
 */

import type { Agent } from "../types";
import { ChatDataEmbedded } from "./components";

export const chatData: Agent = {
  id: "sql-generator",
  name: "智能问数",
  description: "自然语言生成SQL，智能查询数据",
  category: "risk",
  icon: "fa-database",
  color: "blue" as const,
  displayMode: "inline",
  navigatePath: "/chatdata",
  guide: {
    title: "智能问数",
    description: "用自然语言描述你的查询需求，我会帮你生成SQL语句",
    icon: "fa-database",
    iconColor: "blue",
  },
  component: ChatDataEmbedded,
  componentProps: {
    agentId: "chat_data",
    toolType: "chatdata",
  },
};
