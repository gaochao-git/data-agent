import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import type { EvidenceItem, HumanReviewData } from "../types";
import { mergeClassNames } from "../utils";
import { LawContentPopover, useLawContentPopover } from "./LawContentPopover";
import { AttachmentViewer, useAttachmentViewer } from "./AttachmentViewer";
import { API_BASE } from "@/lib/config";

// 审核数据类型
type ReviewEditData =
  | { violations: any[] }
  | { inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }
  | { files: Array<{ file_index: number; inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }> }
  | { evidence_list: EvidenceItem[]; basic_info: Record<string, string> };

interface ReviewEditorProps {
  reviewData: HumanReviewData;
  onConfirm: (data: ReviewEditData, isEdited: boolean) => void;
  onCancel: () => void;
  onUploadEvidence?: (file: File, violationId: number) => Promise<void>;
  threadId: string;  // 用于构建文件下载路径
  panelHeightClass: string;
}

// 审核类型配置
const REVIEW_TYPE_CONFIG: Record<string, { title: string; icon: string }> = {
  extract: { title: "确认解析结果", icon: "fa-file-lines" },
  split: { title: "确认拆分结果", icon: "fa-scissors" },
  analyze: { title: "确认分析结果", icon: "fa-magnifying-glass-chart" },
  retrieve: { title: "确认检索结果", icon: "fa-scale-balanced" },
  evidence: { title: "确认取证记录", icon: "fa-clipboard-list" },
};

export function ReviewEditor({
  reviewData,
  onConfirm,
  onCancel,
  onUploadEvidence,
  threadId,
  panelHeightClass,
}: ReviewEditorProps) {
  const reviewType = reviewData.review_type;
  const config = REVIEW_TYPE_CONFIG[reviewType] || { title: "审核确认", icon: "fa-check-circle" };

  // 本地编辑状态
  const [isEditing, setIsEditing] = useState(false);

  // 本地跟踪每个违法行为的取证记录表上传状态（用于证据目录报告）
  const [violationUploadStatus, setViolationUploadStatus] = useState<Record<number, boolean>>(
    reviewData.violation_upload_status || {}
  );

  // 附件查看器状态
  const { viewerState: attachmentViewerState, openViewer: openAttachmentViewer, closeViewer: closeAttachmentViewer } = useAttachmentViewer();

  // extract 类型的编辑状态（支持单个文件或多个文件）
  const [editedExtractData, setEditedExtractData] = useState<
    { inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string } |
    { files: Array<{ file_index: number; inspection_content: string; violation_facts: string; inspected_person?: string; inspection_time?: string }> }
  >({
    inspection_content: "",
    violation_facts: "",
  });

  // 当前展开的文件索引集合（多文件模式，支持同时展开多个）
  const [expandedFileIndexes, setExpandedFileIndexes] = useState<Set<number>>(new Set([1]));

  // violations 类型的编辑状态 (split/analyze/retrieve)
  const [editedViolations, setEditedViolations] = useState<any[]>([]);
  const [expandedViolationIds, setExpandedViolationIds] = useState<Set<number>>(new Set());

  // evidence 类型的编辑状态
  const [editedEvidenceData, setEditedEvidenceData] = useState<{
    evidence_list: EvidenceItem[];
    basic_info: Record<string, string>;
  }>({ evidence_list: [], basic_info: {} });

  // 初始化编辑数据
  useEffect(() => {
    console.log("[ReviewEditor] 初始化审核数据:", {
      review_type: reviewType,
      current_data: reviewData.current_data,
      has_files: !!reviewData.current_data?.files,
      files_count: reviewData.current_data?.files?.length || 0
    });

    setIsEditing(false);
    // 默认展开第一个违法行为
    if (reviewData.current_data?.violations?.length > 0) {
      setExpandedViolationIds(new Set([reviewData.current_data.violations[0].violation_id]));
    } else {
      setExpandedViolationIds(new Set());
    }

    if (reviewType === "extract") {
      // 检查是多文件还是单文件
      if (reviewData.current_data?.files) {
        // 多文件模式
        console.log("[ReviewEditor] 使用多文件模式，文件数:", reviewData.current_data.files.length);
        setEditedExtractData({
          files: JSON.parse(JSON.stringify(reviewData.current_data.files))
        });
        // 默认展开第一个文件
        setExpandedFileIndexes(new Set([reviewData.current_data.files[0]?.file_index || 1]));
      } else {
        // 单文件模式
        console.log("[ReviewEditor] 使用单文件模式");
        setEditedExtractData({
          inspection_content: reviewData.current_data?.inspection_content || "",
          violation_facts: reviewData.current_data?.violation_facts || "",
        });
        setExpandedFileIndexes(new Set());
      }
    } else if (reviewType === "evidence") {
      setEditedEvidenceData({
        evidence_list: JSON.parse(JSON.stringify(reviewData.current_data?.evidence_list || [])),
        basic_info: { ...reviewData.current_data?.basic_info } || {},
      });
    } else if (reviewData.current_data?.violations) {
      setEditedViolations(JSON.parse(JSON.stringify(reviewData.current_data.violations)));
    }
  }, [reviewData, reviewType]);

  // 只有检索结果审核（retrieve）才显示完整的5项字段
  const showFullFields = reviewType === "retrieve";

  // 法规原文查看器
  const {
    viewerState,
    openViewer,
    closeViewer,
  } = useLawContentPopover();

  // 打开法规原文查看器
  const handleViewLawDocument = (
    e: React.MouseEvent<HTMLButtonElement>,
    lawName: string,
    lawItemName: string
  ) => {
    openViewer(lawName, lawItemName, e.currentTarget);
  };

  // ============ Extract 类型处理函数 ============
  const handleExtractFieldChange = (field: "inspection_content" | "violation_facts", value: string) => {
    setIsEditing(true);
    setEditedExtractData((prev) => ({ ...prev, [field]: value }));
  };

  const handleMultiFileFieldChange = (fileIndex: number, field: "inspection_content" | "violation_facts" | "inspected_person" | "inspection_time", value: string) => {
    setIsEditing(true);
    setEditedExtractData((prev) => {
      if ('files' in prev) {
        const updated = { ...prev };
        updated.files = prev.files.map(file =>
          file.file_index === fileIndex ? { ...file, [field]: value } : file
        );
        return updated;
      }
      return prev;
    });
  };

  // ============ Violations 类型处理函数 ============
  const handleViolationFieldChange = (index: number, field: string, value: any) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      updated[index] = { ...updated[index], [field]: value };
      return updated;
    });
  };

  const handleDeleteViolation = (index: number) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = prev.filter((_, i) => i !== index);
      return updated.map((v, i) => ({ ...v, violation_id: i + 1 }));
    });
  };

  const handleAddViolation = () => {
    setIsEditing(true);
    setEditedViolations((prev) => [
      ...prev,
      { violation_id: prev.length + 1, title: "", facts: "" },
    ]);
  };

  const handleLawItemChange = (
    violationIndex: number,
    field: "legal_basis" | "penalty_clause",
    lawIndex: number,
    lawField: "law_name" | "law_item_name" | "law_item_content",
    value: string
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const lawItems = [...(updated[violationIndex][field] || [])];
      lawItems[lawIndex] = { ...lawItems[lawIndex], [lawField]: value };
      updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems };
      return updated;
    });
  };

  const handleDeleteLawItem = (
    violationIndex: number,
    field: "legal_basis" | "penalty_clause",
    lawIndex: number
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const lawItems = [...(updated[violationIndex][field] || [])];
      lawItems.splice(lawIndex, 1);
      updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems };
      return updated;
    });
  };

  const handleAddLawItem = (violationIndex: number, field: "legal_basis" | "penalty_clause") => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const lawItems = [...(updated[violationIndex][field] || [])];
      lawItems.push({ law_name: "", law_item_name: "", law_item_content: "" });
      updated[violationIndex] = { ...updated[violationIndex], [field]: lawItems };
      return updated;
    });
  };

  const handleStringArrayItemChange = (
    violationIndex: number,
    field: "discretion_standard" | "rectification_measures" | "penalty_suggestion",
    itemIndex: number,
    value: string
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const items = [...(updated[violationIndex][field] || [])];
      items[itemIndex] = value;
      updated[violationIndex] = { ...updated[violationIndex], [field]: items };
      return updated;
    });
  };

  const handleDeleteStringArrayItem = (
    violationIndex: number,
    field: "discretion_standard" | "rectification_measures" | "penalty_suggestion",
    itemIndex: number
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const items = [...(updated[violationIndex][field] || [])];
      items.splice(itemIndex, 1);
      updated[violationIndex] = { ...updated[violationIndex], [field]: items };
      return updated;
    });
  };

  const handleAddStringArrayItem = (
    violationIndex: number,
    field: "discretion_standard" | "rectification_measures" | "penalty_suggestion"
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const items = [...(updated[violationIndex][field] || [])];
      items.push("");
      updated[violationIndex] = { ...updated[violationIndex], [field]: items };
      return updated;
    });
  };

  // ============ 证据列表处理函数（用于违法行为中的证据） ============
  const handleViolationEvidenceFieldChange = (
    violationIndex: number,
    evidenceIndex: number,
    field: keyof EvidenceItem,
    value: string
  ) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const evidenceList = [...(updated[violationIndex].evidence_list || [])];
      evidenceList[evidenceIndex] = { ...evidenceList[evidenceIndex], [field]: value };
      updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList };
      return updated;
    });
  };

  const handleDeleteViolationEvidence = (violationIndex: number, evidenceIndex: number) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const evidenceList = [...(updated[violationIndex].evidence_list || [])];
      evidenceList.splice(evidenceIndex, 1);
      updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList };
      return updated;
    });
  };

  const handleAddViolationEvidence = (violationIndex: number) => {
    setIsEditing(true);
    setEditedViolations((prev) => {
      const updated = [...prev];
      const evidenceList = [...(updated[violationIndex].evidence_list || [])];
      evidenceList.push({
        evidence_name_code: "",
        evidence_source: "",
        evidence_form: "",
        evidence_truth: "",
      });
      updated[violationIndex] = { ...updated[violationIndex], evidence_list: evidenceList };
      return updated;
    });
  };

  // 处理证据文件上传并解析
  const handleEvidenceFileUpload = async (violationIndex: number, file: File) => {
    try {
      // 校验文件类型
      const fileExt = file.name.toLowerCase().split('.').pop();
      if (fileExt !== 'docx' && fileExt !== 'wps') {
        alert('只能上传 Word 文档（.docx 或 .wps 格式）');
        return;
      }

      // 创建 FormData
      const formData = new FormData();
      formData.append('file', file);
      formData.append('file_type_hint', '取证记录表');

      // 需要获取当前违法行为对应的 group_id 和 violation_id
      const currentViolation = editedViolations[violationIndex];
      let groupId = null;
      const violationId = currentViolation.violation_id;

      if (currentViolation.source_files && currentViolation.source_files.length > 0) {
        // 尝试从第一个文件获取 group_id
        groupId = currentViolation.source_files[0].group_id;
      }

      // 如果有 group_id，说明是事实认定书流程，需要传递 group_id
      // 如果没有 group_id，说明是检查报告流程，需要传递 violation_id
      if (groupId) {
        formData.append('group_id', groupId);
      } else if (violationId) {
        formData.append('violation_id', violationId.toString());
      }

      // 调用上传 API（保存文件并更新状态）
      const response = await fetch(`${API_BASE}/api/conversations/${threadId}/files`, {
        method: 'POST',
        body: formData,
      });

      const result = await response.json();

      if (result.status === 'error') {
        alert(result.msg || '文件上传失败');
        return;
      }

      // 上传成功，从返回的数据中获取解析结果
      const uploadedFile = result.data;
      console.log(`[取证记录表上传] 成功:`, uploadedFile);

      // 立即更新当前违法行为的数据
      setIsEditing(true);
      setEditedViolations((prev) => {
        const updated = [...prev];
        const currentViolation = updated[violationIndex];

        // 添加取证记录表到附件列表
        const newAttachment = {
          file_id: uploadedFile.file_id,
          filename: uploadedFile.file_name,
          file_name: uploadedFile.file_name,
          file_type: '取证记录表',
          upload_time: new Date().toISOString(),
          group_id: groupId,
        };

        if (!currentViolation.source_files) {
          currentViolation.source_files = [];
        }

        // 检查是否已存在取证记录表，避免重复添加
        const hasEvidenceRecord = currentViolation.source_files.some(
          (f: any) => f.file_type === '取证记录表'
        );

        if (!hasEvidenceRecord) {
          currentViolation.source_files.push(newAttachment);
        } else {
          // 替换已有的取证记录表
          const index = currentViolation.source_files.findIndex(
            (f: any) => f.file_type === '取证记录表'
          );
          currentViolation.source_files[index] = newAttachment;
        }

        // 如果后端返回了解析后的证据列表，也更新到界面
        if (uploadedFile.parsed_data?.evidence_list) {
          currentViolation.evidence_list = uploadedFile.parsed_data.evidence_list;
        }

        updated[violationIndex] = currentViolation;
        return updated;
      });

      console.log(`[取证记录表] 已添加到附件列表，文件ID: ${uploadedFile.file_id}`);

      // 更新该违法行为的取证记录表上传状态
      setViolationUploadStatus((prev) => ({
        ...prev,
        [violationId]: true,
      }));
    } catch (error) {
      console.error('[证据上传] 失败:', error);
      alert('证据上传失败，请稍后重试');
    }
  };

  // ============ Evidence 类型处理函数 ============
  const handleEvidenceFieldChange = (index: number, field: keyof EvidenceItem, value: string) => {
    setIsEditing(true);
    setEditedEvidenceData((prev) => {
      const newList = [...prev.evidence_list];
      newList[index] = { ...newList[index], [field]: value };
      return { ...prev, evidence_list: newList };
    });
  };

  const handleDeleteEvidence = (index: number) => {
    setIsEditing(true);
    setEditedEvidenceData((prev) => ({
      ...prev,
      evidence_list: prev.evidence_list.filter((_, i) => i !== index),
    }));
  };

  const handleAddEvidence = () => {
    setIsEditing(true);
    setEditedEvidenceData((prev) => ({
      ...prev,
      evidence_list: [
        ...prev.evidence_list,
        { evidence_name_code: "", evidence_source: "", evidence_form: "", evidence_truth: "" },
      ],
    }));
  };

  // ============ 通用处理函数 ============
  const handleConfirm = () => {
    if (reviewType === "extract") {
      onConfirm(editedExtractData, isEditing);
    } else if (reviewType === "evidence") {
      onConfirm(editedEvidenceData, isEditing);
    } else {
      onConfirm({ violations: editedViolations }, isEditing);
    }
  };

  return (
    <div className={`flex flex-col bg-white dark:bg-gray-900 ${panelHeightClass}`}>
      {/* 头部 */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-gray-200 dark:border-gray-700 bg-amber-50 dark:bg-amber-900/20">
        <div className="flex items-center gap-2">
          <div className="flex h-6 w-6 items-center justify-center rounded-full bg-amber-500 text-white">
            <i className={`fa-solid ${config.icon} text-xs`}></i>
          </div>
          <h3 className="text-sm font-medium text-gray-900 dark:text-white">
            {config.title}
          </h3>
          <span className="text-xs text-amber-600 dark:text-amber-400 bg-amber-100 dark:bg-amber-900/40 px-2 py-0.5 rounded">
            待确认
          </span>
        </div>
      </div>

      {/* 内容区域 - 可滚动 */}
      <div className="flex-1 overflow-auto p-4">
        <div className="space-y-4">
          {/* ============ Extract 类型 ============ */}
          {reviewType === "extract" && (
            <div className="space-y-4">
              {'files' in editedExtractData ? (
                // 多文件模式
                <div className="space-y-3">
                  {editedExtractData.files.map((file) => (
                    <div
                      key={file.file_index}
                      className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
                    >
                      {/* 文件头部 */}
                      <div
                        className="px-4 py-3 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between cursor-pointer"
                        onClick={() => {
                          setExpandedFileIndexes(prev => {
                            const newSet = new Set(prev);
                            if (newSet.has(file.file_index)) {
                              newSet.delete(file.file_index);
                            } else {
                              newSet.add(file.file_index);
                            }
                            return newSet;
                          });
                        }}
                      >
                        <div className="flex items-center gap-3">
                          <span className="w-6 h-6 rounded-full bg-indigo-600 text-white text-xs flex items-center justify-center font-medium">
                            {file.file_index}
                          </span>
                          <span className="text-sm font-medium text-gray-900 dark:text-white">
                            事实认定书 {file.file_index}
                          </span>
                          <span className="text-xs text-gray-500 dark:text-gray-400 truncate max-w-[300px]">
                            {file.inspection_content?.substring(0, 30) || '未填写检查内容'}...
                          </span>
                        </div>
                        <i className={mergeClassNames(
                          "fa-solid fa-chevron-down text-gray-400 transition-transform",
                          expandedFileIndexes.has(file.file_index) ? "rotate-180" : ""
                        )}></i>
                      </div>

                      {/* 文件详情 */}
                      <AnimatePresence>
                        {expandedFileIndexes.has(file.file_index) && (
                          <motion.div
                            initial={{ height: 0, opacity: 0 }}
                            animate={{ height: "auto", opacity: 1 }}
                            exit={{ height: 0, opacity: 0 }}
                            className="overflow-hidden"
                          >
                            <div className="px-4 py-4 space-y-4 border-t border-gray-200 dark:border-gray-700">
                              <div>
                                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                                  被检查人
                                </label>
                                <input
                                  type="text"
                                  value={file.inspected_person || ''}
                                  onChange={(e) => handleMultiFileFieldChange(file.file_index, "inspected_person", e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                                  placeholder="被检查人"
                                />
                              </div>
                              <div>
                                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                                  检查时间
                                </label>
                                <input
                                  type="text"
                                  value={file.inspection_time || ''}
                                  onChange={(e) => handleMultiFileFieldChange(file.file_index, "inspection_time", e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                                  placeholder="检查时间"
                                />
                              </div>
                              <div>
                                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                                  检查内容
                                </label>
                                <input
                                  type="text"
                                  value={file.inspection_content}
                                  onChange={(e) => handleMultiFileFieldChange(file.file_index, "inspection_content", e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                                  placeholder="检查内容"
                                />
                              </div>
                              <div>
                                <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                                  违法事实描述
                                </label>
                                <textarea
                                  value={file.violation_facts}
                                  onChange={(e) => handleMultiFileFieldChange(file.file_index, "violation_facts", e.target.value)}
                                  className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                                  placeholder="违法事实描述"
                                  rows={12}
                                />
                              </div>
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  ))}
                  <div className="text-xs text-gray-500 dark:text-gray-400 text-right">
                    共 {editedExtractData.files.length} 份事实认定书
                  </div>
                </div>
              ) : (
                // 单文件模式
                <div className="space-y-4">
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      检查内容
                    </label>
                    <input
                      type="text"
                      value={editedExtractData.inspection_content}
                      onChange={(e) => handleExtractFieldChange("inspection_content", e.target.value)}
                      className="w-full px-3 py-2.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                      placeholder="检查内容"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">
                      违法事实描述
                    </label>
                    <textarea
                      value={editedExtractData.violation_facts}
                      onChange={(e) => handleExtractFieldChange("violation_facts", e.target.value)}
                      className="w-full px-3 py-2.5 text-sm border border-gray-300 dark:border-gray-600 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent resize-none"
                      placeholder="违法事实描述"
                      rows={12}
                    />
                  </div>
                </div>
              )}
            </div>
          )}

          {/* ============ Evidence 类型 ============ */}
          {reviewType === "evidence" && (
            <div className="space-y-4">
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm min-w-[800px]">
                  <thead>
                    <tr className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400 text-xs">
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-center w-14">序号</th>
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[180px]">证据名称及编号</th>
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[200px]">证据来源</th>
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left w-32">表现形式</th>
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-left min-w-[200px]">真实性说明</th>
                      <th className="border border-gray-200 dark:border-gray-700 px-3 py-2.5 text-center w-14">操作</th>
                    </tr>
                  </thead>
                  <tbody>
                    {editedEvidenceData.evidence_list.map((evidence, index) => (
                      <tr key={index} className="bg-white dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800">
                        <td className="border border-gray-200 dark:border-gray-700 px-3 py-2 text-center text-gray-700 dark:text-gray-300 font-medium">
                          {index + 1}
                        </td>
                        <td className="border border-gray-200 dark:border-gray-700 p-1.5">
                          <input
                            type="text"
                            value={evidence.evidence_name_code}
                            onChange={(e) => handleEvidenceFieldChange(index, "evidence_name_code", e.target.value)}
                            className="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                            placeholder="如：3-4-1-1(1)：XX表"
                          />
                        </td>
                        <td className="border border-gray-200 dark:border-gray-700 p-1.5">
                          <textarea
                            value={evidence.evidence_source}
                            onChange={(e) => handleEvidenceFieldChange(index, "evidence_source", e.target.value)}
                            className="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                            rows={2}
                            placeholder="证据来源"
                          />
                        </td>
                        <td className="border border-gray-200 dark:border-gray-700 p-1.5">
                          <input
                            type="text"
                            value={evidence.evidence_form}
                            onChange={(e) => handleEvidenceFieldChange(index, "evidence_form", e.target.value)}
                            className="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                            placeholder="电子证据"
                          />
                        </td>
                        <td className="border border-gray-200 dark:border-gray-700 p-1.5">
                          <textarea
                            value={evidence.evidence_truth}
                            onChange={(e) => handleEvidenceFieldChange(index, "evidence_truth", e.target.value)}
                            className="w-full px-2 py-1.5 text-sm border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                            rows={2}
                            placeholder="真实性说明"
                          />
                        </td>
                        <td className="border border-gray-200 dark:border-gray-700 px-3 py-2 text-center">
                          <button
                            type="button"
                            onClick={() => handleDeleteEvidence(index)}
                            className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                            title="删除此证据"
                          >
                            <i className="fa-solid fa-trash-can"></i>
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <button
                type="button"
                onClick={handleAddEvidence}
                className="w-full py-2.5 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-2 text-sm"
              >
                <i className="fa-solid fa-plus"></i>
                <span>添加证据</span>
              </button>
              <div className="text-xs text-gray-500 dark:text-gray-400 text-right">
                共 {editedEvidenceData.evidence_list.length} 条证据记录
              </div>
            </div>
          )}

          {/* ============ Violations 类型 (split/analyze/retrieve) ============ */}
          {reviewType !== "extract" && reviewType !== "evidence" && (
            <div className="space-y-4">
              {/* 案件基本信息（仅检索审核时显示，全局显示一次） */}
              {showFullFields && editedViolations.length > 0 && (
                <div className="p-4 bg-blue-50 dark:bg-blue-900/20 border border-blue-200 dark:border-blue-800 rounded-lg space-y-3">
                  <div className="flex items-center gap-2 text-sm font-semibold text-blue-900 dark:text-blue-300 mb-3">
                    <i className="fa-solid fa-info-circle"></i>
                    <span>案件基本信息</span>
                  </div>
                  <div className="grid grid-cols-2 gap-3">
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                        被检查人
                      </label>
                      <input
                        type="text"
                        value={editedViolations[0]?.inspected_person || ''}
                        onChange={(e) => {
                          setIsEditing(true);
                          // 更新所有违法行为的 inspected_person
                          setEditedViolations((prev) =>
                            prev.map((v) => ({ ...v, inspected_person: e.target.value }))
                          );
                        }}
                        className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                        placeholder="被检查人名称"
                      />
                    </div>
                    <div>
                      <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1">
                        检查时间
                      </label>
                      <input
                        type="text"
                        value={editedViolations[0]?.inspection_date || ''}
                        onChange={(e) => {
                          setIsEditing(true);
                          // 更新所有违法行为的 inspection_date
                          setEditedViolations((prev) =>
                            prev.map((v) => ({ ...v, inspection_date: e.target.value }))
                          );
                        }}
                        className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-blue-500"
                        placeholder="检查时间"
                      />
                    </div>
                  </div>
                  <div className="text-xs text-blue-600 dark:text-blue-400 flex items-start gap-2 mt-2">
                    <i className="fa-solid fa-lightbulb mt-0.5"></i>
                    <span>提示：基本信息为全局信息，将应用于整个案件的所有检查内容</span>
                  </div>
                </div>
              )}

              {/* 违法行为列表 */}
              <div className="space-y-3">
                {editedViolations.map((violation, index) => (
                  <div
                    key={violation.violation_id}
                    className="border border-gray-200 dark:border-gray-700 rounded-lg overflow-hidden"
                  >
                  {/* 违法行为头部 */}
                  <div
                    className="px-4 py-3 bg-gray-50 dark:bg-gray-800/50 flex items-center justify-between cursor-pointer"
                    onClick={() => {
                      setExpandedViolationIds(prev => {
                        const newSet = new Set(prev);
                        if (newSet.has(violation.violation_id)) {
                          newSet.delete(violation.violation_id);
                        } else {
                          newSet.add(violation.violation_id);
                        }
                        return newSet;
                      });
                    }}
                  >
                    <div className="flex items-center gap-3">
                      <span className="w-6 h-6 rounded-full bg-indigo-600 text-white text-xs flex items-center justify-center font-medium">
                        {violation.violation_id}
                      </span>
                      <span className="text-sm font-medium text-gray-900 dark:text-white">
                        检查内容 {violation.violation_id}
                      </span>
                      <span className="text-xs text-gray-500 dark:text-gray-400 truncate max-w-[300px]">
                        {violation.title || violation.facts}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      {/* 附件按钮 - 显示文件名 */}
                      {violation.source_files && violation.source_files.length > 0 && (
                        <div className="flex items-center gap-1">
                          {violation.source_files.map((file: any, fileIdx: number) => (
                            <button
                              key={fileIdx}
                              type="button"
                              onClick={(e) => {
                                e.stopPropagation();
                                openAttachmentViewer(
                                  file.filename || file.file_name || file.file_type || '附件',
                                  file.file_id,
                                  threadId
                                );
                              }}
                              className="px-2 py-1 text-xs text-blue-600 dark:text-blue-400 hover:text-blue-700 dark:hover:text-blue-300 hover:bg-blue-50 dark:hover:bg-blue-900/20 rounded transition-colors flex items-center gap-1 border border-blue-200 dark:border-blue-800"
                              title={`查看 ${file.filename || file.file_name || file.file_type || '附件'}`}
                            >
                              <i className="fa-solid fa-file-word text-blue-500"></i>
                              <span className="max-w-[120px] truncate">
                                {file.file_type || file.filename || file.file_name || `附件${fileIdx + 1}`}
                              </span>
                            </button>
                          ))}
                        </div>
                      )}

                      <button
                        type="button"
                        onClick={(e) => {
                          e.stopPropagation();
                          handleDeleteViolation(index);
                        }}
                        className="p-1.5 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                      >
                        <i className="fa-solid fa-trash-can text-xs"></i>
                      </button>
                      <i className={mergeClassNames(
                        "fa-solid fa-chevron-down text-gray-400 transition-transform",
                        expandedViolationIds.has(violation.violation_id) ? "rotate-180" : ""
                      )}></i>
                    </div>
                  </div>

                  {/* 违法行为详情 */}
                  <AnimatePresence>
                    {expandedViolationIds.has(violation.violation_id) && (
                      <motion.div
                        initial={{ height: 0, opacity: 0 }}
                        animate={{ height: "auto", opacity: 1 }}
                        exit={{ height: 0, opacity: 0 }}
                        className="overflow-hidden"
                      >
                        <div className="px-4 py-4 space-y-4 border-t border-gray-200 dark:border-gray-700">
                          {/* 原始事实认定 */}
                          <div>
                            <label className="block text-xs font-medium text-gray-700 dark:text-gray-300 mb-1.5">
                              原始事实认定
                            </label>
                            <textarea
                              value={violation.facts}
                              onChange={(e) => handleViolationFieldChange(index, "facts", e.target.value)}
                              className="w-full px-3 py-2 text-sm border border-gray-200 dark:border-gray-700 rounded-lg bg-white dark:bg-gray-800 text-gray-900 dark:text-white resize-none focus:outline-none focus:ring-2 focus:ring-indigo-500 focus:border-transparent"
                              rows={8}
                            />
                          </div>

                          {/* 取证记录（仅证据目录报告显示） */}
                          {reviewData.requires_evidence_upload && (
                            <div className="border border-dashed border-gray-300 dark:border-gray-600 rounded-lg p-3">
                              <div className="mb-3">
                                <label className="text-xs font-medium text-gray-700 dark:text-gray-300 flex items-center gap-1.5 mb-3">
                                  <i className="fa-solid fa-clipboard-list text-gray-500"></i>
                                  取证记录
                                  <span className="text-red-500 text-xs">*必传</span>
                                </label>

                                {/* 上传状态提示 */}
                                <div className={`mb-2 px-2 py-1 rounded text-xs ${
                                  violationUploadStatus[violation.violation_id]
                                    ? 'bg-green-50 dark:bg-green-900/20 text-green-700 dark:text-green-400'
                                    : 'bg-amber-50 dark:bg-amber-900/20 text-amber-700 dark:text-amber-400'
                                }`}>
                                  {violationUploadStatus[violation.violation_id] ? (
                                    <><i className="fa-solid fa-check-circle mr-1"></i>已上传取证记录表</>
                                  ) : (
                                    <><i className="fa-solid fa-exclamation-triangle mr-1"></i>请上传取证记录表</>
                                  )}
                                </div>

                                {(!violation.evidence_list || violation.evidence_list.length === 0) && (
                                  <div className="flex justify-center py-4">
                                    <label className="cursor-pointer">
                                      <input
                                        type="file"
                                        accept=".docx,.wps"
                                        onChange={async (e) => {
                                          const file = e.target.files?.[0];
                                          if (file) {
                                            await handleEvidenceFileUpload(index, file);
                                            e.target.value = '';
                                          }
                                        }}
                                        className="hidden"
                                      />
                                      <div className="px-3 py-2 text-sm bg-indigo-500 text-white rounded hover:bg-indigo-600 transition-colors flex items-center gap-2">
                                        <i className="fa-solid fa-upload"></i>
                                        上传取证记录表
                                      </div>
                                    </label>
                                  </div>
                                )}
                              </div>

                              {/* 证据列表表格 */}
                              {violation.evidence_list && violation.evidence_list.length > 0 && (
                              <div className="space-y-2">
                                <label className="block text-xs text-gray-600 dark:text-gray-400">证据清单</label>
                                <div className="overflow-x-auto">
                                  <table className="w-full border-collapse text-xs min-w-[700px]">
                                    <thead>
                                      <tr className="bg-gray-50 dark:bg-gray-800 text-gray-600 dark:text-gray-400">
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center w-10">序号</th>
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[140px]">证据名称及编号</th>
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[150px]">证据来源</th>
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left w-24">表现形式</th>
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-left min-w-[150px]">真实性说明</th>
                                        <th className="border border-gray-200 dark:border-gray-700 px-2 py-1.5 text-center w-12">操作</th>
                                      </tr>
                                    </thead>
                                    <tbody>
                                      {violation.evidence_list.map((evidence: any, evidenceIdx: number) => (
                                        <tr key={evidenceIdx} className="bg-white dark:bg-gray-800/50 hover:bg-gray-50 dark:hover:bg-gray-800">
                                          <td className="border border-gray-200 dark:border-gray-700 px-2 py-1 text-center text-gray-700 dark:text-gray-300 font-medium">
                                            {evidenceIdx + 1}
                                          </td>
                                          <td className="border border-gray-200 dark:border-gray-700 p-1">
                                            <input
                                              type="text"
                                              value={evidence.evidence_name_code || ''}
                                              onChange={(e) => handleViolationEvidenceFieldChange(index, evidenceIdx, "evidence_name_code", e.target.value)}
                                              className="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                              placeholder="如：3-4-1-1(1)：XX表"
                                            />
                                          </td>
                                          <td className="border border-gray-200 dark:border-gray-700 p-1">
                                            <textarea
                                              value={evidence.evidence_source || ''}
                                              onChange={(e) => handleViolationEvidenceFieldChange(index, evidenceIdx, "evidence_source", e.target.value)}
                                              className="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                              rows={1}
                                              placeholder="证据来源"
                                            />
                                          </td>
                                          <td className="border border-gray-200 dark:border-gray-700 p-1">
                                            <input
                                              type="text"
                                              value={evidence.evidence_form || ''}
                                              onChange={(e) => handleViolationEvidenceFieldChange(index, evidenceIdx, "evidence_form", e.target.value)}
                                              className="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                              placeholder="电子证据"
                                            />
                                          </td>
                                          <td className="border border-gray-200 dark:border-gray-700 p-1">
                                            <textarea
                                              value={evidence.evidence_truth || ''}
                                              onChange={(e) => handleViolationEvidenceFieldChange(index, evidenceIdx, "evidence_truth", e.target.value)}
                                              className="w-full px-1.5 py-1 text-xs border border-transparent hover:border-gray-300 dark:hover:border-gray-600 bg-transparent text-gray-900 dark:text-white resize-none focus:outline-none focus:border-indigo-500 focus:ring-1 focus:ring-indigo-500 rounded transition-colors"
                                              rows={1}
                                              placeholder="真实性说明"
                                            />
                                          </td>
                                          <td className="border border-gray-200 dark:border-gray-700 px-2 py-1 text-center">
                                            <button
                                              type="button"
                                              onClick={() => handleDeleteViolationEvidence(index, evidenceIdx)}
                                              className="p-1 text-gray-400 hover:text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded transition-colors"
                                              title="删除此证据"
                                            >
                                              <i className="fa-solid fa-trash-can text-xs"></i>
                                            </button>
                                          </td>
                                        </tr>
                                      ))}
                                    </tbody>
                                  </table>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => handleAddViolationEvidence(index)}
                                  className="w-full py-1.5 border border-dashed border-gray-300 dark:border-gray-600 rounded text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1"
                                >
                                  <i className="fa-solid fa-plus"></i>
                                  添加证据
                                </button>
                                <div className="text-xs text-gray-500 dark:text-gray-400 text-right">
                                  共 {violation.evidence_list.length} 条证据记录
                                </div>
                              </div>
                              )}
                            </div>
                          )}

                          {/* 检索结果（retrieve 类型显示完整字段） */}
                          {showFullFields && (
                            <>
                              {/* 违反条款 */}
                              <div>
                                <label className="block text-sm font-semibold text-gray-900 dark:text-white mb-2">违反条款</label>
                                <div className="space-y-2">
                                  {(violation.legal_basis || []).map((law: any, i: number) => (
                                    <div key={i} className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg space-y-2">
                                      <div className="flex items-center gap-2">
                                        <input type="text" value={law.law_name} onChange={(e) => handleLawItemChange(index, "legal_basis", i, "law_name", e.target.value)} placeholder="法律名称" className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500" />
                                        <input type="text" value={law.law_item_name} onChange={(e) => handleLawItemChange(index, "legal_basis", i, "law_item_name", e.target.value)} placeholder="条款编号" className="w-24 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500" />
                                        <button type="button" onClick={(e) => handleViewLawDocument(e, law.law_name, law.law_item_name)} className="p-1.5 text-indigo-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded transition-colors" title="查看原文"><i className="fa-solid fa-book-open text-xs"></i></button>
                                        <button type="button" onClick={() => handleDeleteLawItem(index, "legal_basis", i)} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors"><i className="fa-solid fa-trash-can text-xs"></i></button>
                                      </div>
                                      <textarea value={law.law_item_content} onChange={(e) => handleLawItemChange(index, "legal_basis", i, "law_item_content", e.target.value)} placeholder="条款内容" className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500" rows={2} />
                                    </div>
                                  ))}
                                  {(!violation.legal_basis || violation.legal_basis.length === 0) && <div className="text-xs text-gray-400 text-center py-2">暂无</div>}
                                  <button type="button" onClick={() => handleAddLawItem(index, "legal_basis")} className="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1">
                                    <i className="fa-solid fa-plus"></i>添加违反条款
                                  </button>
                                </div>
                              </div>

                              {/* 处罚条款 */}
                              <div>
                                <label className="block text-sm font-semibold text-gray-900 dark:text-white mb-2">处罚条款</label>
                                <div className="space-y-2">
                                  {(violation.penalty_clause || []).map((law: any, i: number) => (
                                    <div key={i} className="p-3 bg-gray-50 dark:bg-gray-800/50 rounded-lg space-y-2">
                                      <div className="flex items-center gap-2">
                                        <input type="text" value={law.law_name} onChange={(e) => handleLawItemChange(index, "penalty_clause", i, "law_name", e.target.value)} placeholder="法律名称" className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500" />
                                        <input type="text" value={law.law_item_name} onChange={(e) => handleLawItemChange(index, "penalty_clause", i, "law_item_name", e.target.value)} placeholder="条款编号" className="w-24 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-900 dark:text-white focus:outline-none focus:ring-1 focus:ring-indigo-500" />
                                        <button type="button" onClick={(e) => handleViewLawDocument(e, law.law_name, law.law_item_name)} className="p-1.5 text-indigo-500 hover:text-indigo-600 hover:bg-indigo-50 dark:hover:bg-indigo-900/30 rounded transition-colors" title="查看原文"><i className="fa-solid fa-book-open text-xs"></i></button>
                                        <button type="button" onClick={() => handleDeleteLawItem(index, "penalty_clause", i)} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors"><i className="fa-solid fa-trash-can text-xs"></i></button>
                                      </div>
                                      <textarea value={law.law_item_content} onChange={(e) => handleLawItemChange(index, "penalty_clause", i, "law_item_content", e.target.value)} placeholder="条款内容" className="w-full px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-600 dark:text-gray-400 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500" rows={2} />
                                    </div>
                                  ))}
                                  {(!violation.penalty_clause || violation.penalty_clause.length === 0) && <div className="text-xs text-gray-400 text-center py-2">暂无</div>}
                                  <button type="button" onClick={() => handleAddLawItem(index, "penalty_clause")} className="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1">
                                    <i className="fa-solid fa-plus"></i>添加处罚条款
                                  </button>
                                </div>
                              </div>

                              {/* 裁量基准 */}
                              <div>
                                <label className="block text-sm font-semibold text-gray-900 dark:text-white mb-2">裁量基准</label>
                                <div className="space-y-2">
                                  {(violation.discretion_standard || []).map((item: string, i: number) => (
                                    <div key={i} className="flex items-start gap-2">
                                      <span className="text-gray-400 text-xs mt-2">•</span>
                                      <textarea value={item} onChange={(e) => handleStringArrayItemChange(index, "discretion_standard", i, e.target.value)} className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500" rows={1} />
                                      <button type="button" onClick={() => handleDeleteStringArrayItem(index, "discretion_standard", i)} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors"><i className="fa-solid fa-trash-can text-xs"></i></button>
                                    </div>
                                  ))}
                                  {(!violation.discretion_standard || violation.discretion_standard.length === 0) && <div className="text-xs text-gray-400 text-center py-2">暂无</div>}
                                  <button type="button" onClick={() => handleAddStringArrayItem(index, "discretion_standard")} className="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1">
                                    <i className="fa-solid fa-plus"></i>添加裁量基准
                                  </button>
                                </div>
                              </div>

                              {/* 整改措施 */}
                              <div>
                                <label className="block text-sm font-semibold text-gray-900 dark:text-white mb-2">整改措施</label>
                                <div className="space-y-2">
                                  {(violation.rectification_measures || []).map((item: string, i: number) => (
                                    <div key={i} className="flex items-start gap-2">
                                      <span className="text-gray-400 text-xs mt-2">•</span>
                                      <textarea value={item} onChange={(e) => handleStringArrayItemChange(index, "rectification_measures", i, e.target.value)} className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500" rows={1} />
                                      <button type="button" onClick={() => handleDeleteStringArrayItem(index, "rectification_measures", i)} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors"><i className="fa-solid fa-trash-can text-xs"></i></button>
                                    </div>
                                  ))}
                                  {(!violation.rectification_measures || violation.rectification_measures.length === 0) && <div className="text-xs text-gray-400 text-center py-2">暂无</div>}
                                  <button type="button" onClick={() => handleAddStringArrayItem(index, "rectification_measures")} className="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1">
                                    <i className="fa-solid fa-plus"></i>添加整改措施
                                  </button>
                                </div>
                              </div>

                              {/* 处罚建议 */}
                              <div>
                                <label className="block text-sm font-semibold text-gray-900 dark:text-white mb-2">处罚建议</label>
                                <div className="space-y-2">
                                  {(violation.penalty_suggestion || []).map((item: string, i: number) => (
                                    <div key={i} className="flex items-start gap-2">
                                      <span className="text-gray-400 text-xs mt-2">•</span>
                                      <textarea value={item} onChange={(e) => handleStringArrayItemChange(index, "penalty_suggestion", i, e.target.value)} className="flex-1 px-2 py-1.5 text-xs border border-gray-200 dark:border-gray-700 rounded bg-white dark:bg-gray-800 text-gray-700 dark:text-gray-300 resize-none focus:outline-none focus:ring-1 focus:ring-indigo-500" rows={1} />
                                      <button type="button" onClick={() => handleDeleteStringArrayItem(index, "penalty_suggestion", i)} className="p-1.5 text-gray-400 hover:text-red-500 transition-colors"><i className="fa-solid fa-trash-can text-xs"></i></button>
                                    </div>
                                  ))}
                                  {(!violation.penalty_suggestion || violation.penalty_suggestion.length === 0) && <div className="text-xs text-gray-400 text-center py-2">暂无</div>}
                                  <button type="button" onClick={() => handleAddStringArrayItem(index, "penalty_suggestion")} className="w-full py-2 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-xs text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50/50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-1">
                                    <i className="fa-solid fa-plus"></i>添加处罚建议
                                  </button>
                                </div>
                              </div>
                            </>
                          )}
                        </div>
                      </motion.div>
                    )}
                  </AnimatePresence>
                </div>
                ))}

                {/* 添加检查内容按钮（仅拆分审核和分析审核时显示） */}
                {(reviewType === "split" || reviewType === "analyze") && (
                  <button
                    type="button"
                    onClick={handleAddViolation}
                    className="w-full py-2.5 border border-dashed border-gray-300 dark:border-gray-600 rounded-lg text-gray-500 dark:text-gray-400 hover:border-indigo-400 hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors flex items-center justify-center gap-2 text-sm"
                  >
                    <i className="fa-solid fa-plus"></i>
                    <span>添加检查内容</span>
                  </button>
                )}

                <div className="text-xs text-gray-500 dark:text-gray-400 text-right">
                  共 {editedViolations.length} 条检查内容
                </div>
              </div>
            </div>
          )}
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="flex items-center justify-between px-4 py-3 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
        <div className="text-xs text-gray-500 dark:text-gray-400">
          {isEditing && (
            <span className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
              <i className="fa-solid fa-pen"></i>
              已修改
            </span>
          )}
        </div>
        <div className="flex items-center gap-3">
          <button
            type="button"
            onClick={onCancel}
            className="px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-300 bg-white dark:bg-gray-700 border border-gray-300 dark:border-gray-600 rounded-lg hover:bg-gray-50 dark:hover:bg-gray-600 transition-colors"
          >
            取消任务
          </button>
          <button
            type="button"
            onClick={handleConfirm}
            disabled={reviewData.requires_evidence_upload && !reviewData.all_evidence_uploaded && !Object.values(violationUploadStatus).every(Boolean)}
            className={`px-5 py-2 text-sm font-medium text-white rounded-lg transition-colors flex items-center gap-2 ${
              reviewData.requires_evidence_upload && !reviewData.all_evidence_uploaded && !Object.values(violationUploadStatus).every(Boolean)
                ? 'bg-gray-400 dark:bg-gray-600 cursor-not-allowed'
                : 'bg-indigo-600 hover:bg-indigo-700'
            }`}
            title={
              reviewData.requires_evidence_upload && !reviewData.all_evidence_uploaded && !Object.values(violationUploadStatus).every(Boolean)
                ? '请为每个违法行为上传取证记录表'
                : undefined
            }
          >
            <i className="fa-solid fa-check"></i>
            {isEditing ? "确认修改" : "确认继续"}
          </button>
        </div>
      </div>

      {/* 法规原文查看器 */}
      {viewerState && (
        <LawContentPopover
          lawName={viewerState.lawName}
          lawItemName={viewerState.lawItemName}
          isOpen={viewerState.isOpen}
          onClose={closeViewer}
          initialPosition={viewerState.position}
        />
      )}

      {/* 附件查看器 */}
      {attachmentViewerState && (
        <AttachmentViewer
          fileName={attachmentViewerState.fileName}
          fileId={attachmentViewerState.fileId}
          threadId={attachmentViewerState.threadId}
          isOpen={attachmentViewerState.isOpen}
          onClose={closeAttachmentViewer}
        />
      )}
    </div>
  );
}
