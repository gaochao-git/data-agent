import {
  BaseConversation,
  type AgentConversationProps,
  type SuggestionPill,
} from "./BaseConversation";

const defaultFeatureTags = [
  {
    id: "legal-search",
    label: "法律检索",
  },
];

const AlwaysOnFeatureTags = () => (
  <div className="flex flex-wrap items-center gap-2 text-left">
    {defaultFeatureTags.map((tag) => (
      <button
        key={tag.id}
        type="button"
        aria-pressed="true"
        disabled
        className="inline-flex items-center space-x-2 rounded-full border border-blue-200 bg-blue-50 px-4 py-1.5 text-xs font-semibold text-blue-600 shadow-sm cursor-not-allowed"
        title={`${tag.label}（默认启用）`}
      >
        <i className="fa-solid fa-scale-balanced text-[11px]"></i>
        <span>{tag.label}</span>
      </button>
    ))}
  </div>
);

const suggestionPills: SuggestionPill[] = [
  {
    id: "merchant-risk",
    label: "特约商户套码",
    content: "特约商户套码风险案例",
  },
  {
    id: "aml",
    label: "反洗钱整改",
    content: "反洗钱可疑交易分析",
  },
  {
    id: "prepaid",
    label: "预付卡违规",
    content: "预付卡资金挪用处罚案例",
  },
];

export default function CaseKnowledgeConversation({
  onAttachmentStateChange: _onAttachmentStateChange,
  ...rest
}: AgentConversationProps) {
  return (
    <BaseConversation
      {...rest}
      introDescription=""
      suggestionPills={suggestionPills}
      highlights={[]}
      showHighlights={false}
      composerFooterStart={<AlwaysOnFeatureTags />}
      composerPlaceholder="描述您的业务情境，我将从专业角度为您解答。"
      followUpPlaceholder=""
      showComposerActions={false}
    />
  );
}
