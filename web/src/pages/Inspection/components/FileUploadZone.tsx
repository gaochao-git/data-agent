import { useState, useCallback, DragEvent, ChangeEvent } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { mergeClassNames } from "../utils";

interface FileUploadZoneProps {
  onFileSelect: (file: File) => void;
  isUploading?: boolean;
  accept?: string;
}

export function FileUploadZone({
  onFileSelect,
  isUploading = false,
  accept = ".pdf,.doc,.docx,.txt"
}: FileUploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false);

  const handleDragEnter = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);
  }, []);

  const handleDragOver = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
  }, []);

  const handleDrop = useCallback((e: DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragging(false);

    const files = e.dataTransfer.files;
    if (files && files.length > 0) {
      onFileSelect(files[0]);
    }
  }, [onFileSelect]);

  const handleFileInputChange = useCallback((e: ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files;
    if (files && files.length > 0) {
      onFileSelect(files[0]);
    }
  }, [onFileSelect]);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -20 }}
      transition={{ duration: 0.3 }}
      className="flex justify-center"
    >
      <div className="w-full max-w-2xl">
        <div
          onDragEnter={handleDragEnter}
          onDragLeave={handleDragLeave}
          onDragOver={handleDragOver}
          onDrop={handleDrop}
          className={mergeClassNames(
            "relative rounded-2xl border-2 border-dashed p-12 text-center transition-all",
            isDragging
              ? "border-indigo-500 bg-indigo-50/50 dark:border-indigo-400 dark:bg-indigo-900/20"
              : "border-gray-300 bg-white dark:border-gray-600 dark:bg-gray-800/50",
            isUploading && "pointer-events-none opacity-60"
          )}
        >
          <AnimatePresence mode="wait">
            {isUploading ? (
              <motion.div
                key="uploading"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-col items-center gap-4"
              >
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-indigo-100 dark:bg-indigo-900/40">
                  <i className="fa-solid fa-spinner fa-spin text-2xl text-indigo-600 dark:text-indigo-400"></i>
                </div>
                <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
                  正在处理文件...
                </p>
              </motion.div>
            ) : (
              <motion.div
                key="ready"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="flex flex-col items-center gap-4"
              >
                <div className="flex h-16 w-16 items-center justify-center rounded-full bg-indigo-100 dark:bg-indigo-900/40">
                  <i className="fa-solid fa-cloud-arrow-up text-2xl text-indigo-600 dark:text-indigo-400"></i>
                </div>

                <div className="space-y-2">
                  <p className="text-sm font-medium text-gray-700 dark:text-gray-300">
                    拖拽文件到此处，或点击选择文件
                  </p>
                  <p className="text-xs text-gray-500 dark:text-gray-400">
                    支持 PDF、Word、TXT 格式
                  </p>
                </div>

                <label className="cursor-pointer">
                  <input
                    type="file"
                    accept={accept}
                    onChange={handleFileInputChange}
                    className="hidden"
                    disabled={isUploading}
                  />
                  <span className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-6 py-2.5 text-sm font-medium text-white shadow-sm transition-all hover:bg-indigo-700 dark:bg-indigo-500 dark:hover:bg-indigo-600">
                    <i className="fa-solid fa-folder-open"></i>
                    选择文件
                  </span>
                </label>
              </motion.div>
            )}
          </AnimatePresence>
        </div>
      </div>
    </motion.div>
  );
}
