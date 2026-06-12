import { motion, AnimatePresence } from "framer-motion";
import type { ViolationFileGroup } from "../types";
import { ViolationFileGroupsDisplay } from "./ViolationFileGroupsDisplay";

interface ViolationFileManagementModalProps {
  isOpen: boolean;
  onClose: () => void;
  fileGroups: ViolationFileGroup[];
  uploadingGroupIds: Record<string, boolean>;
  onUploadFactDetermination: (file: File, threadId?: string | null, skipLimitCheck?: boolean) => Promise<void>;
  onDeleteGroup: (groupId: string) => Promise<void>;
  onClearAll: () => Promise<void>;
}

export function ViolationFileManagementModal({
  isOpen,
  onClose,
  fileGroups,
  uploadingGroupIds,
  onUploadFactDetermination,
  onDeleteGroup,
  onClearAll,
}: ViolationFileManagementModalProps) {
  if (!isOpen) return null;

  return (
    <AnimatePresence>
      {isOpen && (
        <>
          {/* 遮罩层 */}
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            className="fixed inset-0 bg-black/50 z-50"
          />

          {/* 弹窗内容 */}
          <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.95 }}
              className="bg-white dark:bg-gray-800 rounded-xl shadow-2xl w-full max-w-3xl max-h-[80vh] overflow-hidden flex flex-col"
            >
              {/* 标题栏 */}
              <div className="flex items-center justify-between px-6 py-4 border-b border-gray-200 dark:border-gray-700">
                <h2 className="text-lg font-semibold text-gray-900 dark:text-white">
                  违法行为文件管理
                </h2>
                <button
                  type="button"
                  onClick={onClose}
                  className="text-gray-400 hover:text-gray-600 dark:hover:text-gray-300 transition-colors"
                >
                  <i className="fa-solid fa-xmark text-xl"></i>
                </button>
              </div>

              {/* 内容区域 */}
              <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {/* 文件分组列表 */}
                {fileGroups.length > 0 && (
                  <ViolationFileGroupsDisplay
                    fileGroups={fileGroups}
                    onDeleteGroup={onDeleteGroup}
                    onClearAll={onClearAll}
                  />
                )}

                {/* 上传按钮区域 */}
                {fileGroups.length < 10 && (
                  <div className={fileGroups.length === 0 ? "flex items-center justify-center min-h-[300px]" : ""}>
                    <label className="block">
                      <input
                        type="file"
                        accept=".docx,.wps"
                        multiple
                        onChange={async (e) => {
                          const files = Array.from(e.target.files || []);
                          if (files.length > 0) {
                            // 检查是否超过限制
                            const remainingSlots = 10 - fileGroups.length;
                            if (files.length > remainingSlots) {
                              alert(`最多只能上传 ${remainingSlots} 个文件（当前已有 ${fileGroups.length} 个）`);
                              e.target.value = '';
                              return;
                            }

                            // 批量上传：第一个文件会创建新会话，后续文件复用同一个会话
                            let sharedThreadId: string | null = null;

                            // 依次上传所有文件
                            for (let i = 0; i < files.length; i++) {
                              const file = files[i];
                              try {
                                // @ts-ignore - skipLimitCheck参数
                                const usedThreadId = await onUploadFactDetermination(file, sharedThreadId, true);

                                // 保存 threadId，后续文件使用同一个会话
                                if (!sharedThreadId && usedThreadId) {
                                  sharedThreadId = usedThreadId;
                                }
                              } catch (error) {
                                console.error(`批量上传第 ${i + 1} 个文件失败:`, error);
                                // 继续上传下一个文件
                              }
                            }
                            e.target.value = '';
                          }
                        }}
                        className="hidden"
                        disabled={uploadingGroupIds['new']}
                      />
                      <div className={`flex flex-col items-center gap-3 px-6 py-6 rounded-lg border-2 border-dashed cursor-pointer transition-all ${
                        uploadingGroupIds['new']
                          ? 'border-indigo-500 bg-indigo-50 dark:border-indigo-600 dark:bg-indigo-900/20'
                          : 'border-gray-300 bg-white hover:bg-gray-50 hover:border-indigo-400 hover:shadow-sm dark:border-gray-600 dark:bg-gray-800/30 dark:hover:bg-gray-700/50'
                      }`}>
                        <i className={`fa-solid ${uploadingGroupIds['new'] ? 'fa-spinner fa-spin' : 'fa-cloud-arrow-up'} text-3xl ${
                          uploadingGroupIds['new'] ? 'text-indigo-500' : 'text-gray-400 dark:text-gray-500'
                        }`}></i>
                        <div className="text-center">
                          <div className="text-sm font-medium text-gray-900 dark:text-gray-100">
                            {uploadingGroupIds['new'] ? '正在上传...' : '上传事实认定书'}
                          </div>
                          <div className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                            支持 .docx 和 .wps 格式，可一次选择多个文件
                          </div>
                          <div className="text-xs text-gray-400 dark:text-gray-500 mt-0.5">
                            每个文件对应一个违法行为（最多 {10 - fileGroups.length} 个）
                          </div>
                        </div>
                      </div>
                    </label>
                  </div>
                )}
              </div>

              {/* 底部操作栏 */}
              <div className="flex items-center justify-end px-6 py-4 border-t border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800/50">
                <button
                  type="button"
                  onClick={onClose}
                  className="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg transition-colors"
                >
                  完成
                </button>
              </div>
            </motion.div>
          </div>
        </>
      )}
    </AnimatePresence>
  );
}
