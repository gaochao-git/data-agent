/**
 * 通用助手常量配置
 *
 * 注意：智能体相关配置请修改 agentRegistry.ts
 */

// 模型配置
export const MODELS = [
  {
    id: "deepseek-r1",
    name: "DeepSeek-R1",
    description: "DeepSeek 推理模型"
  },
  {
    id: "deepseek-v3",
    name: "DeepSeek-V3",
    description: "DeepSeek V3 模型"
  },
  {
    id: "qwen3-30b",
    name: "Qwen3-30B-A3B-Instruct-2507",
    description: "通义千问 3 模型"
  }
] as const;

// 面板高度类
export const panelHeightClass = "h-[calc(100vh-64px)]";
