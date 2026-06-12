import { useState, useEffect, useRef } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { formatDetailItem } from "../utils";
import type { AnalysisResult, ViolationAnalysis } from "../types";

// 相似案例报告类型
export interface CaseReport {
  id: string;
  name: string; // 报告名称：执法检查报告、责令整改通知书、证据目录、信息简表
  content: string; // 报告内容
}

// 相似案例类型（包含摘要内容和4个报告）
export interface SimilarCase {
  id: string;
  title: string;           // 案例标题
  similarity: number;      // 相似度 0-100
  date: string;            // 案例日期
  // 摘要内容
  violatedClauses: string[];         // 违反条款
  penaltyClauses: string[];          // 处罚条款
  discretionStandard: string[];      // 裁量基准
  penaltySuggestion: string[];       // 处罚建议
  rectificationMeasures: string[];   // 整改措施
  // 4个报告原文
  reports: CaseReport[];
}

interface AnalysisResultMessageProps {
  analysisResult: AnalysisResult;
  isCompleted: boolean;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onReportClick?: (report: CaseReport) => void; // 点击报告时在右侧预览
}

// Tab 类型
type TabType = "analysis" | "similar";

// 辅助函数：确保法律名称有书名号（如果已有则不重复添加）
function ensureBookTitle(lawName: string): string {
  if (!lawName) return "";
  // 如果已经有书名号，直接返回
  if (lawName.startsWith("《") && lawName.endsWith("》")) {
    return lawName;
  }
  // 否则添加书名号
  return `《${lawName}》`;
}

export function AnalysisResultMessage({
  analysisResult,
  isCompleted,
  onLawClick,
  onReportClick,
}: AnalysisResultMessageProps) {
  const [activeTab, setActiveTab] = useState<TabType>("analysis");
  const [expandedItems, setExpandedItems] = useState<Set<number>>(new Set());
  const [similarCases, setSimilarCases] = useState<SimilarCase[]>([]);
  const [loadingSimilar, setLoadingSimilar] = useState(false);
  const [aiTooltip, setAiTooltip] = useState<{ title: string; content: string } | null>(null);

  // 防止重复获取相似案例
  const hasFetchedRef = useRef(false);

  // 当分析完成时自动获取相似案例（只执行一次）
  useEffect(() => {
    if (isCompleted && analysisResult.违法行为分析.length > 0 && !hasFetchedRef.current) {
      hasFetchedRef.current = true;
      fetchSimilarCases();
      // 默认展开所有违法事实分析
      setExpandedItems(new Set(analysisResult.违法行为分析.map(v => v.违法行为编号)));
    }
  }, [isCompleted]);

  const fetchSimilarCases = async () => {
    setLoadingSimilar(true);

    // 从后端返回的历史案例数据中提取
    const backendCases: SimilarCase[] = [];

    analysisResult.违法行为分析.forEach((violation) => {
      if (violation.历史案例 && violation.历史案例.length > 0) {
        violation.历史案例.forEach((caseItem) => {
          backendCases.push({
            id: caseItem.case_id,
            title: caseItem.case_title,
            similarity: Math.round(caseItem.similarity_score * 100),
            date: caseItem.inspection_date || "未知",
            violatedClauses: caseItem.violated_provisions?.map(p =>
              `${ensureBookTitle(p.law_name)}${p.law_item_name}`
            ) || [],
            penaltyClauses: caseItem.penalty_provisions?.map(p =>
              `${ensureBookTitle(p.law_name)}${p.law_item_name}`
            ) || [],
            discretionStandard: caseItem.discretionary_benchmarks || [],
            penaltySuggestion: [caseItem.penalty_result],
            rectificationMeasures: caseItem.rectification_measures || [],
            reports: [
              {
                id: `${caseItem.case_id}-report`,
                name: "检查报告",
                content: `# ${caseItem.case_title}\n\n## 案例摘要\n${caseItem.case_summary}\n\n## 违法事实\n${caseItem.violation_facts}\n\n## 处罚结果\n${caseItem.penalty_result}\n\n## 参考价值\n${caseItem.reference_value}`
              },
            ],
          });
        });
      }
    });

    // 如果有后端案例，使用后端数据；否则使用 mock 数据
    const finalCases = backendCases.length > 0 ? backendCases : [
        {
          id: "case-1",
          title: "某支付公司小微商户信用卡收款超限案",
          similarity: 92,
          date: "2024-08-15",
          violatedClauses: [
            "《非金融机构支付服务管理办法》第三条",
            "《银行卡收单业务管理办法》第二十八条",
          ],
          penaltyClauses: [
            "《非金融机构支付服务管理办法》第四十二条",
            "《银行卡收单业务管理办法》第五十条",
          ],
          discretionStandard: [
            "违规金额超过10万元，属于情节较重",
            "首次违规，配合调查",
          ],
          penaltySuggestion: [
            "责令改正",
            "警告",
            "处罚款人民币3万元",
          ],
          rectificationMeasures: [
            "完成系统整改，对超限商户进行清理",
            "建立日/月累计收款监控机制",
            "定期进行合规检查",
          ],
          reports: [
            { id: "r1", name: "执法检查报告", content: "关于某支付公司小微商户信用卡收款超限的执法检查报告\n\n一、检查基本情况\n检查时间：2024年8月1日至8月15日\n检查对象：某支付公司\n\n二、发现的主要问题\n1. 小微商户日累计收款超限\n2. 月累计收款超限\n...(详细内容)" },
            { id: "r2", name: "责令整改通知书", content: "责令整改通知书\n\n某支付公司：\n经检查发现，你公司存在以下违规问题...\n请在收到本通知书之日起30日内完成整改...(详细内容)" },
            { id: "r3", name: "证据目录", content: "证据目录\n\n序号 | 证据名称 | 证据来源 | 证明内容\n1 | 商户交易流水 | 系统导出 | 证明超限交易事实\n2 | 商户协议 | 档案调取 | 证明商户类型\n...(详细内容)" },
            { id: "r4", name: "信息简表", content: "信息简表\n\n案件名称：某支付公司小微商户信用卡收款超限案\n当事人：某支付公司\n违法事实：小微商户收款超限\n处罚结果：责令改正，警告，并处罚款人民币3万元\n...(详细内容)" },
          ],
        },
        {
          id: "case-2",
          title: "某第三方支付机构收款限额管理违规案",
          similarity: 85,
          date: "2024-06-22",
          violatedClauses: [
            "《非金融机构支付服务管理办法》第三条",
            "《支付机构预付卡业务管理办法》第十五条",
          ],
          penaltyClauses: [
            "《非金融机构支付服务管理办法》第四十二条",
          ],
          discretionStandard: [
            "违规金额超过10万元",
            "存在管理漏洞",
          ],
          penaltySuggestion: [
            "责令改正",
            "警告",
            "处罚款人民币2万元",
          ],
          rectificationMeasures: [
            "建立收款限额实时监控系统",
            "对存量商户进行全面排查",
            "完善商户准入流程",
          ],
          reports: [
            { id: "r5", name: "执法检查报告", content: "执法检查报告内容..." },
            { id: "r6", name: "责令整改通知书", content: "责令整改通知书内容..." },
            { id: "r7", name: "证据目录", content: "证据目录内容..." },
            { id: "r8", name: "信息简表", content: "信息简表内容..." },
          ],
        },
        {
          id: "case-3",
          title: "某科技公司支付业务合规检查案",
          similarity: 78,
          date: "2024-03-10",
          violatedClauses: [
            "《非金融机构支付服务管理办法》第三条",
            "《银行卡收单业务管理办法》第七条",
          ],
          penaltyClauses: [
            "《非金融机构支付服务管理办法》第四十二条",
          ],
          discretionStandard: [
            "多项违规行为并存",
            "情节较为严重",
          ],
          penaltySuggestion: [
            "责令改正",
            "处罚款人民币5万元",
          ],
          rectificationMeasures: [
            "重新梳理业务流程",
            "升级交易监控系统",
            "完善内控制度",
            "对相关责任人进行问责处理",
          ],
          reports: [
            { id: "r9", name: "执法检查报告", content: "执法检查报告内容..." },
            { id: "r10", name: "责令整改通知书", content: "责令整改通知书内容..." },
            { id: "r11", name: "证据目录", content: "证据目录内容..." },
            { id: "r12", name: "信息简表", content: "信息简表内容..." },
          ],
        },
      ];

    // 模拟异步加载
    setTimeout(() => {
      setSimilarCases(finalCases);
      setLoadingSimilar(false);
    }, 300);
  };

  const toggleItem = (id: number) => {
    setExpandedItems((prev) => {
      const newSet = new Set(prev);
      if (newSet.has(id)) {
        newSet.delete(id);
      } else {
        newSet.add(id);
      }
      return newSet;
    });
  };

  const toggleAll = () => {
    if (expandedItems.size === analysisResult.违法行为分析.length) {
      setExpandedItems(new Set());
    } else {
      setExpandedItems(
        new Set(analysisResult.违法行为分析.map((v) => v.违法行为编号))
      );
    }
  };

  return (
    <div className="space-y-3">
      {/* Tab 切换区域 */}
      <div className="flex items-center gap-1 rounded-xl bg-gray-100 p-1 dark:bg-gray-800">
        <button
          onClick={() => setActiveTab("analysis")}
          className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-all ${
            activeTab === "analysis"
              ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white"
              : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
          }`}
        >
          <i className="fa-solid fa-file-lines mr-2"></i>
          违法事实
          <span className="ml-1.5 rounded-full bg-indigo-100 px-1.5 py-0.5 text-xs font-semibold text-gray-900 dark:bg-indigo-900/50 dark:text-white">
            {analysisResult.违法行为数量}
          </span>
        </button>
        <button
          onClick={() => setActiveTab("similar")}
          className={`flex-1 rounded-lg px-4 py-2 text-sm font-medium transition-all ${
            activeTab === "similar"
              ? "bg-white text-gray-900 shadow-sm dark:bg-gray-700 dark:text-white"
              : "text-gray-600 hover:text-gray-900 dark:text-gray-400 dark:hover:text-white"
          }`}
        >
          <i className="fa-solid fa-clone mr-2"></i>
          相似案例
          {similarCases.length > 0 && (
            <span className="ml-1.5 rounded-full bg-amber-100 px-1.5 py-0.5 text-xs font-semibold text-gray-900 dark:bg-amber-900/50 dark:text-white">
              {similarCases.length}
            </span>
          )}
        </button>
      </div>

      {/* Tab 内容区域 */}
      <AnimatePresence mode="wait">
        {activeTab === "analysis" ? (
          <motion.div
            key="analysis"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
          >
            <AnalysisContent
              analysisResult={analysisResult}
              isCompleted={isCompleted}
              expandedItems={expandedItems}
              toggleItem={toggleItem}
              toggleAll={toggleAll}
              onLawClick={onLawClick}
              onShowTooltip={(title, content) => setAiTooltip({ title, content })}
            />
          </motion.div>
        ) : (
          <motion.div
            key="similar"
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -10 }}
            transition={{ duration: 0.2 }}
          >
            <SimilarCasesContent
              cases={similarCases}
              loading={loadingSimilar}
              isCompleted={isCompleted}
              onLawClick={onLawClick}
              onReportClick={onReportClick}
            />
          </motion.div>
        )}
      </AnimatePresence>

      {/* AI解读/违法事实弹窗 */}
      <AnimatePresence>
        {aiTooltip && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 z-[200] flex items-center justify-center bg-black/50 p-4"
            onClick={() => setAiTooltip(null)}
          >
            <motion.div
              initial={{ scale: 0.95, opacity: 0 }}
              animate={{ scale: 1, opacity: 1 }}
              exit={{ scale: 0.95, opacity: 0 }}
              className="max-h-[80vh] w-full max-w-lg overflow-auto rounded-xl bg-white p-5 shadow-2xl dark:bg-gray-800"
              onClick={(e) => e.stopPropagation()}
            >
              <div className="mb-3 flex items-center justify-between">
                <h3 className="flex items-center gap-2 text-base font-semibold text-gray-900 dark:text-white">
                  <i className="fa-solid fa-circle-info text-indigo-500"></i>
                  {aiTooltip.title}
                </h3>
                <button
                  onClick={() => setAiTooltip(null)}
                  className="rounded-lg p-1.5 text-gray-400 hover:bg-gray-100 hover:text-gray-600 dark:hover:bg-gray-700"
                >
                  <i className="fa-solid fa-xmark"></i>
                </button>
              </div>
              <p className="text-sm leading-relaxed text-gray-700 dark:text-gray-300 select-text">
                {aiTooltip.content}
              </p>
            </motion.div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// 事实认定内容组件
interface AnalysisContentProps {
  analysisResult: AnalysisResult;
  isCompleted: boolean;
  expandedItems: Set<number>;
  toggleItem: (id: number) => void;
  toggleAll: () => void;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onShowTooltip?: (title: string, content: string) => void;
}

function AnalysisContent({
  analysisResult,
  isCompleted,
  expandedItems,
  toggleItem,
  toggleAll,
  onLawClick,
  onShowTooltip,
}: AnalysisContentProps) {
  return (
    <div className="space-y-3">
      {/* 操作栏 */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-600 dark:text-gray-400">
          共识别出 <span className="font-semibold text-gray-900 dark:text-white">{analysisResult.违法行为数量}</span> 项违法事实
        </p>
        <button
          onClick={toggleAll}
          className="flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-xs font-medium text-gray-600 hover:bg-gray-100 dark:text-gray-400 dark:hover:bg-gray-700 transition-colors"
        >
          <i className={`fa-solid ${expandedItems.size === analysisResult.违法行为分析.length ? "fa-compress-alt" : "fa-expand-alt"}`}></i>
          {expandedItems.size === analysisResult.违法行为分析.length ? "全部折叠" : "全部展开"}
        </button>
      </div>

      {/* 事实认定列表 */}
      {analysisResult.违法行为分析.map((violation) => (
        <ViolationCard
          key={violation.违法行为编号}
          violation={violation}
          isExpanded={expandedItems.has(violation.违法行为编号)}
          isCompleted={isCompleted}
          onToggle={() => toggleItem(violation.违法行为编号)}
          onLawClick={onLawClick}
          onShowTooltip={onShowTooltip}
        />
      ))}
    </div>
  );
}

// 单个违法行为卡片
interface ViolationCardProps {
  violation: ViolationAnalysis;
  isExpanded: boolean;
  isCompleted: boolean;
  onToggle: () => void;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onShowTooltip?: (title: string, content: string) => void;
}

function ViolationCard({
  violation,
  isExpanded,
  isCompleted,
  onToggle,
  onLawClick,
  onShowTooltip,
}: ViolationCardProps) {
  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-shadow hover:shadow-md dark:border-gray-700 dark:bg-gray-800">
      {/* 标题栏 */}
      <div
        className="flex cursor-pointer items-center justify-between gap-3 px-4 py-3 transition-colors hover:bg-gray-50 dark:hover:bg-gray-700/50"
        onClick={onToggle}
      >
        <div className="flex items-center gap-3 min-w-0">
          <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-lg bg-indigo-100 text-sm font-semibold text-indigo-600 dark:bg-indigo-900/50 dark:text-indigo-400">
            {violation.违法行为编号}
          </div>
          <div className="min-w-0 flex-1">
            <h3 className="truncate text-sm font-medium text-gray-900 dark:text-white flex items-center gap-2">
              {violation.原始事实认定.length > 50
                ? violation.原始事实认定.substring(0, 50) + "..."
                : violation.原始事实认定}
              <i
                className="fa-solid fa-circle-info cursor-pointer text-indigo-500 hover:text-indigo-600 text-xs flex-shrink-0"
                onClick={(e) => {
                  e.stopPropagation();
                  onShowTooltip?.(
                    violation.AI扩充 ? "AI 解读" : "违法事实",
                    violation.AI扩充 || violation.原始事实认定
                  );
                }}
              ></i>
            </h3>
          </div>
        </div>
        <i className={`fa-solid ${isExpanded ? "fa-chevron-up" : "fa-chevron-down"} text-gray-400`}></i>
      </div>

      {/* 展开内容 */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.25 }}
            className="overflow-hidden"
          >
            <div className="border-t border-gray-100 dark:border-gray-700">
              {/* 法规条款区域 */}
              <div className="space-y-4 px-4 py-4">
                {/* 违反条款 */}
                <LawSection
                  title="违反条款"
                  items={violation.违反条款}
                  isCompleted={isCompleted}
                  onLawClick={onLawClick}
                />

                {/* 处罚条款 */}
                <LawSection
                  title="处罚条款"
                  items={violation.处罚条款}
                  isCompleted={isCompleted}
                  onLawClick={onLawClick}
                />

                {/* 其他信息（裁量基准、处罚建议、整改措施） */}
                <div className="grid gap-4 md:grid-cols-3">
                  {violation.裁量基准?.length > 0 && (
                    <InfoList
                      title="裁量基准"
                      icon="fa-ruler"
                      iconColor="text-amber-500"
                      items={violation.裁量基准}
                    />
                  )}
                  {violation.处罚建议?.length > 0 && (
                    <InfoList
                      title="处罚建议"
                      icon="fa-lightbulb"
                      iconColor="text-yellow-500"
                      items={violation.处罚建议}
                    />
                  )}
                  {violation.整改措施?.length > 0 && (
                    <InfoList
                      title="整改措施"
                      icon="fa-wrench"
                      iconColor="text-blue-500"
                      items={violation.整改措施}
                    />
                  )}
                </div>

                {/* ⭐ 证据列表 */}
                {violation.证据列表 && violation.证据列表.length > 0 && (
                  <div className="space-y-3">
                    <h5 className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
                      <i className="fa-solid fa-folder-open text-indigo-500"></i>
                      证据列表
                    </h5>
                    <EvidenceTable evidence={violation.证据列表} />
                  </div>
                )}
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// 法规条款区块
interface LawSectionProps {
  title: string;
  items: { law_name: string; law_item_name: string; law_item_content: string }[];
  isCompleted: boolean;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
}

function LawSection({ title, items, isCompleted, onLawClick }: LawSectionProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
      <h4 className="mb-2 text-sm font-bold text-gray-900 dark:text-white">
        {title}
      </h4>
      <ul className="space-y-1">
        {items && items.length > 0 ? (
          items.map((item, index) => (
            <li key={index} className="flex gap-2 text-sm">
              <span className="text-gray-400 flex-shrink-0">•</span>
              <span
                onClick={() => onLawClick?.(item)}
                className="text-indigo-600 hover:text-indigo-800 dark:text-indigo-400 dark:hover:text-indigo-300 cursor-pointer hover:underline"
              >
                {ensureBookTitle(item.law_name)}{item.law_item_name}
              </span>
            </li>
          ))
        ) : (
          <li className="text-sm text-gray-500 dark:text-gray-400">
            {isCompleted ? "暂无" : "检索中..."}
          </li>
        )}
      </ul>
    </div>
  );
}

// 信息列表组件
interface InfoListProps {
  title: string;
  icon: string;
  iconColor: string;
  items: string[];
}

function InfoList({ title, icon, iconColor, items }: InfoListProps) {
  return (
    <div className="rounded-lg bg-gray-50 p-3 dark:bg-gray-900/50">
      <h5 className="mb-2 flex items-center gap-2 text-xs font-semibold text-gray-700 dark:text-gray-300">
        <i className={`fa-solid ${icon} ${iconColor}`}></i>
        {title}
      </h5>
      <ul className="space-y-1">
        {items.map((item, idx) => (
          <li key={idx} className="flex gap-2 text-xs text-gray-600 dark:text-gray-400">
            <span className={iconColor}>•</span>
            <span className="flex-1">{formatDetailItem(item)}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// 相似案例内容组件
interface SimilarCasesContentProps {
  cases: SimilarCase[];
  loading: boolean;
  isCompleted: boolean;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onReportClick?: (report: CaseReport) => void;
}

function SimilarCasesContent({ cases, loading, isCompleted, onLawClick, onReportClick }: SimilarCasesContentProps) {
  if (!isCompleted) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
        <i className="fa-solid fa-hourglass-half mb-3 text-2xl"></i>
        <p className="text-sm">等待事实认定分析完成...</p>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
        <i className="fa-solid fa-spinner fa-spin mb-3 text-2xl"></i>
        <p className="text-sm">正在检索相似案例...</p>
      </div>
    );
  }

  if (cases.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 text-gray-500 dark:text-gray-400">
        <i className="fa-solid fa-folder-open mb-3 text-2xl"></i>
        <p className="text-sm">暂无相似案例</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <p className="text-sm text-gray-600 dark:text-gray-400">
        找到 <span className="font-semibold text-gray-900 dark:text-white">{cases.length}</span> 个相似案例，点击查看详情
      </p>
      {cases.map((caseItem) => (
        <SimilarCaseCard key={caseItem.id} caseItem={caseItem} onLawClick={onLawClick} onReportClick={onReportClick} />
      ))}
    </div>
  );
}

// 相似案例卡片
interface SimilarCaseCardProps {
  caseItem: SimilarCase;
  onLawClick?: (law: { law_name: string; law_item_name: string; law_item_content: string }) => void;
  onReportClick?: (report: CaseReport) => void; // 点击报告时在右侧预览
}

function SimilarCaseCard({ caseItem, onLawClick, onReportClick }: SimilarCaseCardProps) {
  const getSimilarityColor = (similarity: number) => {
    if (similarity >= 90) return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-400";
    if (similarity >= 80) return "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400";
    return "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300";
  };

  // 将摘要中的法律条款转换为可点击的格式
  const parseLawFromText = (text: string): { law_name: string; law_item_name: string; law_item_content: string } | null => {
    // 匹配格式：《xxx》第xxx条
    const match = text.match(/《(.+?)》(第.+?条)/);
    if (match) {
      return {
        law_name: match[1],
        law_item_name: match[2],
        law_item_content: text,
      };
    }
    return null;
  };

  return (
    <div className="overflow-hidden rounded-xl border border-gray-200 bg-white shadow-sm transition-all hover:shadow-md dark:border-gray-700 dark:bg-gray-800">
      {/* 案例标题和操作区域 */}
      <div className="flex items-center gap-3 px-4 py-3">
        <div className="min-w-0 flex-1">
          <h3 className="text-sm font-medium text-gray-900 dark:text-white line-clamp-2">
            {caseItem.title}
          </h3>
        </div>
        <span className={`rounded-full px-2 py-0.5 text-xs font-semibold flex-shrink-0 ${getSimilarityColor(caseItem.similarity)}`}>
          {caseItem.similarity}%
        </span>
        {caseItem.reports && caseItem.reports.length > 0 && (
          <button
            onClick={() => onReportClick?.(caseItem.reports[0])}
            className="flex-shrink-0 rounded-lg border border-indigo-200 bg-white px-3 py-1.5 text-xs font-medium text-indigo-600 transition-all hover:bg-indigo-50 hover:border-indigo-300 dark:border-indigo-800 dark:bg-gray-800 dark:text-indigo-400 dark:hover:bg-indigo-900/20"
          >
            <i className="fa-solid fa-file-lines mr-1.5"></i>
            查看报告
          </button>
        )}
      </div>
    </div>
  );
}

// 案例摘要区块组件
interface CaseSummarySectionProps {
  title: string;
  children: React.ReactNode;
}

function CaseSummarySection({ title, children }: CaseSummarySectionProps) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-3 dark:border-gray-700 dark:bg-gray-800">
      <h4 className="mb-2 text-sm font-bold text-gray-900 dark:text-white">
        {title}
      </h4>
      {children}
    </div>
  );
}

// ⭐ 历史案例卡片（用于分析结果中的历史案例）
import type { HistoricalCase } from "../types";

interface HistoricalCaseCardProps {
  caseItem: HistoricalCase;
}

function HistoricalCaseCard({ caseItem }: HistoricalCaseCardProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const getSimilarityColor = (similarity: number) => {
    if (similarity >= 0.9) return "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/50 dark:text-emerald-400";
    if (similarity >= 0.8) return "bg-amber-100 text-amber-700 dark:bg-amber-900/50 dark:text-amber-400";
    return "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-300";
  };

  return (
    <div className="overflow-hidden rounded-lg border border-gray-200 bg-gray-50/50 dark:border-gray-700 dark:bg-gray-900/30">
      {/* 标题栏 */}
      <div
        className="flex cursor-pointer items-center justify-between gap-3 px-3 py-2.5 transition-colors hover:bg-gray-100 dark:hover:bg-gray-800"
        onClick={() => setIsExpanded(!isExpanded)}
      >
        <div className="min-w-0 flex-1">
          <h6 className="truncate text-sm font-medium text-gray-900 dark:text-white">
            {caseItem.case_title}
          </h6>
          <p className="mt-0.5 text-xs text-gray-500 dark:text-gray-400">
            案例编号: {caseItem.case_id}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <span className={`rounded-full px-2 py-0.5 text-xs font-semibold ${getSimilarityColor(caseItem.similarity_score)}`}>
            {(caseItem.similarity_score * 100).toFixed(0)}%
          </span>
          <i className={`fa-solid ${isExpanded ? "fa-chevron-up" : "fa-chevron-down"} text-gray-400 text-xs`}></i>
        </div>
      </div>

      {/* 展开内容 */}
      <AnimatePresence>
        {isExpanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="border-t border-gray-200 px-3 py-3 dark:border-gray-700">
              <div className="space-y-2 text-xs">
                <div>
                  <span className="font-semibold text-gray-700 dark:text-gray-300">案例摘要：</span>
                  <p className="mt-1 text-gray-600 dark:text-gray-400">{caseItem.case_summary}</p>
                </div>
                <div>
                  <span className="font-semibold text-gray-700 dark:text-gray-300">违法事实：</span>
                  <p className="mt-1 text-gray-600 dark:text-gray-400">{caseItem.violation_facts}</p>
                </div>
                <div>
                  <span className="font-semibold text-gray-700 dark:text-gray-300">处罚结果：</span>
                  <p className="mt-1 text-gray-600 dark:text-gray-400">{caseItem.penalty_result}</p>
                </div>
                <div>
                  <span className="font-semibold text-gray-700 dark:text-gray-300">参考价值：</span>
                  <p className="mt-1 text-gray-600 dark:text-gray-400">{caseItem.reference_value}</p>
                </div>
              </div>
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

// ⭐ 证据列表表格
import type { EvidenceItem } from "../types";

interface EvidenceTableProps {
  evidence: EvidenceItem[];
}

function EvidenceTable({ evidence }: EvidenceTableProps) {
  return (
    <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
      <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
        <thead className="bg-gray-50 dark:bg-gray-800">
          <tr>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">序号</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据名称及编号</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据来源</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">证据形式</th>
            <th className="px-3 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">真实性说明</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
          {evidence.map((item, idx) => (
            <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
              <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{idx + 1}</td>
              <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{item.evidence_name_code}</td>
              <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{item.evidence_source}</td>
              <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{item.evidence_form}</td>
              <td className="px-3 py-2 text-xs text-gray-700 dark:text-gray-300">{item.evidence_truth}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
