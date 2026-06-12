/**
 * 可编辑的分析结果视图
 * 允许用户查看和编辑所有分析数据字段
 */

import { useState } from "react";
import type { ViolationAnalysis, LawItem, HistoricalCase, EvidenceItem } from "../types";

interface EditableAnalysisViewProps {
  violation: ViolationAnalysis;
  onSave: (updated: ViolationAnalysis) => void;
  onCancel: () => void;
}

export function EditableAnalysisView({
  violation,
  onSave,
  onCancel,
}: EditableAnalysisViewProps) {
  const [editedData, setEditedData] = useState<ViolationAnalysis>(violation);

  const handleSave = () => {
    onSave(editedData);
  };

  return (
    <div className="space-y-6 p-4">
      {/* 头部 */}
      <div className="flex items-center justify-between border-b border-gray-200 pb-4 dark:border-gray-700">
        <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
          编辑违法行为 #{editedData.违法行为编号}
        </h2>
        <div className="flex gap-2">
          <button
            onClick={onCancel}
            className="rounded-lg border border-gray-300 px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
          >
            取消
          </button>
          <button
            onClick={handleSave}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-700"
          >
            保存修改
          </button>
        </div>
      </div>

      {/* 违法事实 */}
      <EditableSection title="违法事实" icon="📋">
        <textarea
          value={editedData.原始事实认定}
          onChange={(e) =>
            setEditedData({ ...editedData, 原始事实认定: e.target.value })
          }
          className="w-full rounded-lg border border-gray-300 p-3 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          rows={4}
          placeholder="输入违法事实..."
        />
      </EditableSection>

      {/* AI 扩充 */}
      <EditableSection title="AI 扩充说明" icon="🤖">
        <textarea
          value={editedData.AI扩充}
          onChange={(e) =>
            setEditedData({ ...editedData, AI扩充: e.target.value })
          }
          className="w-full rounded-lg border border-gray-300 p-3 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          rows={3}
          placeholder="输入AI扩充内容..."
        />
      </EditableSection>

      {/* 违反条款 */}
      <EditableLawList
        title="违反条款"
        icon="⚖️"
        items={editedData.违反条款}
        onChange={(items) => setEditedData({ ...editedData, 违反条款: items })}
      />

      {/* 处罚条款 */}
      <EditableLawList
        title="处罚条款"
        icon="🔨"
        items={editedData.处罚条款}
        onChange={(items) => setEditedData({ ...editedData, 处罚条款: items })}
      />

      {/* 裁量基准 */}
      <EditableStringList
        title="裁量基准"
        icon="📊"
        items={editedData.裁量基准}
        onChange={(items) => setEditedData({ ...editedData, 裁量基准: items })}
        placeholder="添加裁量基准..."
      />

      {/* 整改措施 */}
      <EditableStringList
        title="整改措施"
        icon="🔧"
        items={editedData.整改措施}
        onChange={(items) => setEditedData({ ...editedData, 整改措施: items })}
        placeholder="添加整改措施..."
      />

      {/* 处罚建议 */}
      <EditableStringList
        title="处罚建议"
        icon="💡"
        items={editedData.处罚建议}
        onChange={(items) => setEditedData({ ...editedData, 处罚建议: items })}
        placeholder="添加处罚建议..."
      />

      {/* 历史案例（只读，可移除） */}
      {editedData.历史案例 && editedData.历史案例.length > 0 && (
        <HistoricalCasesList
          cases={editedData.历史案例}
          onRemove={(index) => {
            const updated = [...editedData.历史案例];
            updated.splice(index, 1);
            setEditedData({ ...editedData, 历史案例: updated });
          }}
        />
      )}

      {/* 证据列表 */}
      {editedData.证据列表 && editedData.证据列表.length > 0 && (
        <EditableEvidenceList
          evidence={editedData.证据列表}
          onChange={(items) => setEditedData({ ...editedData, 证据列表: items })}
        />
      )}
    </div>
  );
}

// ========== 子组件 ==========

interface EditableSectionProps {
  title: string;
  icon: string;
  children: React.ReactNode;
}

function EditableSection({ title, icon, children }: EditableSectionProps) {
  return (
    <div className="space-y-2">
      <h3 className="flex items-center gap-2 text-sm font-semibold text-gray-700 dark:text-gray-300">
        <span>{icon}</span>
        {title}
      </h3>
      {children}
    </div>
  );
}

// 可编辑的法律条款列表
interface EditableLawListProps {
  title: string;
  icon: string;
  items: LawItem[];
  onChange: (items: LawItem[]) => void;
}

function EditableLawList({ title, icon, items, onChange }: EditableLawListProps) {
  const [isAdding, setIsAdding] = useState(false);
  const [newItem, setNewItem] = useState<LawItem>({
    law_name: "",
    law_item_name: "",
    law_item_content: "",
  });

  const handleAdd = () => {
    if (newItem.law_name && newItem.law_item_name) {
      onChange([...items, newItem]);
      setNewItem({ law_name: "", law_item_name: "", law_item_content: "" });
      setIsAdding(false);
    }
  };

  const handleRemove = (index: number) => {
    const updated = [...items];
    updated.splice(index, 1);
    onChange(updated);
  };

  return (
    <EditableSection title={title} icon={icon}>
      <div className="space-y-2">
        {items.map((item, idx) => (
          <div
            key={idx}
            className="rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800"
          >
            <div className="flex items-start justify-between">
              <div className="flex-1 space-y-1">
                <p className="text-sm font-medium text-gray-900 dark:text-white">
                  《{item.law_name}》{item.law_item_name}
                </p>
                <p className="text-xs text-gray-600 dark:text-gray-400">
                  {item.law_item_content}
                </p>
              </div>
              <button
                onClick={() => handleRemove(idx)}
                className="ml-2 rounded p-1 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
                title="删除"
              >
                <i className="fa-solid fa-trash text-xs"></i>
              </button>
            </div>
          </div>
        ))}

        {isAdding ? (
          <div className="space-y-2 rounded-lg border border-indigo-300 bg-indigo-50 p-3 dark:border-indigo-700 dark:bg-indigo-900/20">
            <input
              type="text"
              value={newItem.law_name}
              onChange={(e) => setNewItem({ ...newItem, law_name: e.target.value })}
              placeholder="法律名称 (如: 支付服务管理办法)"
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
            />
            <input
              type="text"
              value={newItem.law_item_name}
              onChange={(e) => setNewItem({ ...newItem, law_item_name: e.target.value })}
              placeholder="条款编号 (如: 第三条)"
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
            />
            <textarea
              value={newItem.law_item_content}
              onChange={(e) => setNewItem({ ...newItem, law_item_content: e.target.value })}
              placeholder="条款内容..."
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
              rows={2}
            />
            <div className="flex gap-2">
              <button
                onClick={handleAdd}
                className="rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700"
              >
                确定
              </button>
              <button
                onClick={() => {
                  setIsAdding(false);
                  setNewItem({ law_name: "", law_item_name: "", law_item_content: "" });
                }}
                className="rounded border border-gray-300 px-3 py-1 text-xs text-gray-700 hover:bg-gray-50 dark:border-gray-600 dark:text-gray-300 dark:hover:bg-gray-800"
              >
                取消
              </button>
            </div>
          </div>
        ) : (
          <button
            onClick={() => setIsAdding(true)}
            className="w-full rounded-lg border-2 border-dashed border-gray-300 p-2 text-sm text-gray-600 hover:border-indigo-500 hover:text-indigo-600 dark:border-gray-600 dark:text-gray-400 dark:hover:border-indigo-500 dark:hover:text-indigo-400"
          >
            <i className="fa-solid fa-plus mr-1"></i>
            添加条款
          </button>
        )}
      </div>
    </EditableSection>
  );
}

// 可编辑的字符串列表（用于裁量基准、整改措施等）
interface EditableStringListProps {
  title: string;
  icon: string;
  items: string[];
  onChange: (items: string[]) => void;
  placeholder: string;
}

function EditableStringList({
  title,
  icon,
  items,
  onChange,
  placeholder,
}: EditableStringListProps) {
  const [newItem, setNewItem] = useState("");

  const handleAdd = () => {
    if (newItem.trim()) {
      onChange([...items, newItem.trim()]);
      setNewItem("");
    }
  };

  const handleRemove = (index: number) => {
    const updated = [...items];
    updated.splice(index, 1);
    onChange(updated);
  };

  const handleEdit = (index: number, value: string) => {
    const updated = [...items];
    updated[index] = value;
    onChange(updated);
  };

  return (
    <EditableSection title={title} icon={icon}>
      <div className="space-y-2">
        {items.map((item, idx) => (
          <div key={idx} className="flex items-center gap-2">
            <span className="text-sm text-gray-500 dark:text-gray-400">{idx + 1}.</span>
            <input
              type="text"
              value={item}
              onChange={(e) => handleEdit(idx, e.target.value)}
              className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
            />
            <button
              onClick={() => handleRemove(idx)}
              className="rounded p-1 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
              title="删除"
            >
              <i className="fa-solid fa-trash text-xs"></i>
            </button>
          </div>
        ))}

        <div className="flex gap-2">
          <input
            type="text"
            value={newItem}
            onChange={(e) => setNewItem(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && handleAdd()}
            placeholder={placeholder}
            className="flex-1 rounded border border-gray-300 px-2 py-1 text-sm dark:border-gray-600 dark:bg-gray-800 dark:text-white"
          />
          <button
            onClick={handleAdd}
            className="rounded bg-indigo-600 px-3 py-1 text-xs text-white hover:bg-indigo-700"
          >
            <i className="fa-solid fa-plus"></i>
          </button>
        </div>
      </div>
    </EditableSection>
  );
}

// 历史案例列表（只读，可移除）
interface HistoricalCasesListProps {
  cases: HistoricalCase[];
  onRemove: (index: number) => void;
}

function HistoricalCasesList({ cases, onRemove }: HistoricalCasesListProps) {
  return (
    <EditableSection title="历史案例参考" icon="📚">
      <div className="space-y-2 text-xs text-gray-600 dark:text-gray-400">
        <p>
          历史案例由系统自动检索提供，如不相关可移除。重新检索将获取新的案例。
        </p>
        {cases.map((caseItem, idx) => (
          <div
            key={idx}
            className="flex items-start justify-between rounded-lg border border-gray-200 bg-gray-50 p-3 dark:border-gray-700 dark:bg-gray-800"
          >
            <div className="flex-1">
              <p className="font-medium text-gray-900 dark:text-white">
                {caseItem.case_title}
              </p>
              <p className="mt-1 text-xs text-gray-600 dark:text-gray-400">
                相似度: {(caseItem.similarity_score * 100).toFixed(0)}% | 案例编号:{" "}
                {caseItem.case_id}
              </p>
            </div>
            <button
              onClick={() => onRemove(idx)}
              className="ml-2 rounded p-1 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
              title="移除此案例"
            >
              <i className="fa-solid fa-times text-xs"></i>
            </button>
          </div>
        ))}
      </div>
    </EditableSection>
  );
}

// 可编辑的证据列表
interface EditableEvidenceListProps {
  evidence: EvidenceItem[];
  onChange: (items: EvidenceItem[]) => void;
}

function EditableEvidenceList({ evidence, onChange }: EditableEvidenceListProps) {
  const handleRemove = (index: number) => {
    const updated = [...evidence];
    updated.splice(index, 1);
    onChange(updated);
  };

  const handleEdit = (index: number, field: keyof EvidenceItem, value: string) => {
    const updated = [...evidence];
    updated[index] = { ...updated[index], [field]: value };
    onChange(updated);
  };

  return (
    <EditableSection title="证据列表" icon="🗂️">
      <div className="overflow-x-auto rounded-lg border border-gray-200 dark:border-gray-700">
        <table className="min-w-full divide-y divide-gray-200 dark:divide-gray-700">
          <thead className="bg-gray-50 dark:bg-gray-800">
            <tr>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                序号
              </th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                证据名称及编号
              </th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                证据来源
              </th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                证据形式
              </th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                真实性说明
              </th>
              <th className="px-2 py-2 text-left text-xs font-semibold text-gray-700 dark:text-gray-300">
                操作
              </th>
            </tr>
          </thead>
          <tbody className="divide-y divide-gray-200 bg-white dark:divide-gray-700 dark:bg-gray-900">
            {evidence.map((item, idx) => (
              <tr key={idx} className="hover:bg-gray-50 dark:hover:bg-gray-800">
                <td className="px-2 py-2 text-xs text-gray-700 dark:text-gray-300">
                  {idx + 1}
                </td>
                <td className="px-2 py-2">
                  <input
                    type="text"
                    value={item.evidence_name_code}
                    onChange={(e) =>
                      handleEdit(idx, "evidence_name_code", e.target.value)
                    }
                    className="w-full rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                  />
                </td>
                <td className="px-2 py-2">
                  <input
                    type="text"
                    value={item.evidence_source}
                    onChange={(e) => handleEdit(idx, "evidence_source", e.target.value)}
                    className="w-full rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                  />
                </td>
                <td className="px-2 py-2">
                  <input
                    type="text"
                    value={item.evidence_form}
                    onChange={(e) => handleEdit(idx, "evidence_form", e.target.value)}
                    className="w-full rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                  />
                </td>
                <td className="px-2 py-2">
                  <input
                    type="text"
                    value={item.evidence_truth}
                    onChange={(e) => handleEdit(idx, "evidence_truth", e.target.value)}
                    className="w-full rounded border border-gray-300 px-2 py-1 text-xs dark:border-gray-600 dark:bg-gray-800 dark:text-white"
                  />
                </td>
                <td className="px-2 py-2">
                  <button
                    onClick={() => handleRemove(idx)}
                    className="rounded p-1 text-red-600 hover:bg-red-50 dark:hover:bg-red-900/20"
                    title="删除"
                  >
                    <i className="fa-solid fa-trash text-xs"></i>
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </EditableSection>
  );
}
