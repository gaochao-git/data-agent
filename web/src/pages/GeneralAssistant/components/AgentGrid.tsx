import { useState } from "react";
import { motion } from "framer-motion";
import { AGENT_CATEGORIES, AGENTS } from "../agentRegistry";
import type { Agent } from "../types";

interface AgentGridProps {
  onAgentSelect?: (agent: Agent) => void;
}

export function AgentGrid({ onAgentSelect }: AgentGridProps) {
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null);
  const [hoveredAgentId, setHoveredAgentId] = useState<string | null>(null);
  const [selectedCategory, setSelectedCategory] = useState<string>("all");

  // 按分类分组智能体
  const getAgentsByCategory = (categoryId: string) => {
    return AGENTS.filter(agent => agent.category === categoryId);
  };

  // 获取非"全部"的分类
  const categories = AGENT_CATEGORIES.filter(cat => cat.id !== "all");

  const handleAgentClick = (agent: Agent) => {
    setSelectedAgentId(agent.id);
    onAgentSelect?.(agent);
  };

  // 分类渐变色配置
  const categoryGradients: Record<string, string> = {
    complaint: 'linear-gradient(246deg, #1790B8 0%, #83DAE4 100%)',
    inspection: 'linear-gradient(246deg, #586CDB 0%, #BCBAF9 100%)',
    expert: 'linear-gradient(246deg, #5694FF 0%, #84D5F3 100%)',
    risk: 'linear-gradient(234deg, #FF782D 0%, #FFA73A 100%)',
  };

  // 渲染单个卡片
  const renderCard = (agent: Agent, index: number) => {
    const isSelected = selectedAgentId === agent.id;
    const isHovered = hoveredAgentId === agent.id;
    const isActive = isSelected || isHovered;
    const hasGradient = categoryGradients[agent.category];

    return (
      <motion.button
        key={agent.id}
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: index * 0.03 }}
        onClick={() => handleAgentClick(agent)}
        onMouseEnter={() => setHoveredAgentId(agent.id)}
        onMouseLeave={() => setHoveredAgentId(null)}
        style={{
          borderRadius: '18px',
          border: isActive ? '1px solid #007EA7' : '1px solid #00000026',
          width: '200px',  // 固定卡片宽度
          background: 'white',
        }}
        className={`group flex flex-col justify-center gap-2 px-3 transition-all h-[90px] ${
          isActive ? "shadow-md" : ""
        }`}
      >
        <div className="flex items-center gap-2">
          <div
            className="flex h-6 w-6 items-center justify-center transition-transform group-hover:scale-110 flex-shrink-0"
            style={{
              background: hasGradient || '#8592A6',
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >
            <i
              className={`fa-solid ${agent.icon} text-xl`}
            ></i>
          </div>
          <span
            className="font-bold text-left"
            style={{
              color: isActive ? '#007EA7' : '#333',
              fontSize: '14px',
            }}
          >
            {agent.name}
          </span>
        </div>
        {agent.description && (
          <span
            className="text-left text-xs line-clamp-1"
            style={{
              color: '#9CA3AF',
            }}
            title={agent.description}
          >
            {agent.description}
          </span>
        )}
      </motion.button>
    );
  };

  // 按分类ID获取分类和智能体
  const getCategoryWithAgents = (catId: string) => {
    const category = categories.find(c => c.id === catId);
    const agents = getAgentsByCategory(catId);
    return { category, agents };
  };

  const inspection = getCategoryWithAgents('inspection');
  const complaint = getCategoryWithAgents('complaint');
  const expert = getCategoryWithAgents('expert');
  const risk = getCategoryWithAgents('risk');

  // 第一行的所有智能体
  const firstRowItems: Agent[] = [];
  [complaint, expert, risk].forEach(({ agents }) => {
    if (agents.length > 0) {
      firstRowItems.push(...agents);
    }
  });

  // 根据选中的分类过滤显示内容
  const shouldShowCategory = (catId: string) => {
    return selectedCategory === "all" || selectedCategory === catId;
  };

  return (
    <div className="flex flex-col gap-4 pb-4 items-center">
      {/* 分类标签栏 */}
      <div className="flex justify-center gap-6 border-b border-gray-200 dark:border-gray-700">
        {AGENT_CATEGORIES.map((cat) => (
          <button
            key={cat.id}
            onClick={() => setSelectedCategory(cat.id)}
            className={`pb-2 px-1 text-sm font-medium transition-all relative ${
              selectedCategory === cat.id
                ? "text-[#007EA7]"
                : "text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-300"
            }`}
          >
            {cat.name}({cat.id === 'all' ? AGENTS.length : getAgentsByCategory(cat.id).length})
            {selectedCategory === cat.id && (
              <motion.div
                layoutId="categoryUnderline"
                className="absolute bottom-0 left-0 right-0 h-0.5 bg-[#007EA7]"
              />
            )}
          </button>
        ))}
      </div>

      {/* 卡片网格 - 每行4个 */}
      <div className="flex flex-wrap gap-4 justify-center" style={{ width: '848px' }}>
        {/* 第一行卡片 */}
        {(selectedCategory === "all" || shouldShowCategory('complaint') || shouldShowCategory('expert') || shouldShowCategory('risk')) && (
          firstRowItems
            .filter(agent => shouldShowCategory(agent.category))
            .map((agent, index) => renderCard(agent, index))
        )}

        {/* 执法检查卡片 */}
        {inspection.agents.length > 0 && shouldShowCategory('inspection') && (
          inspection.agents.map((agent, index) => renderCard(agent, firstRowItems.length + index))
        )}
      </div>
    </div>
  );
}
