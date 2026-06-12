import sendIcon from "@/assets/send.png";
import sendOkIcon from "@/assets/send-ok.png";
import stopOkIcon from "@/assets/stop-ok.png";
import uploadIcon from "@/assets/upload.png";
import { useEffect, useMemo, useState } from "react";
import { DeepThinkIcon } from "./ThinkingBlock";

interface ComposerProps {
  footerTagText: string;
  inputValue: string;
  placeholderText: string;
  onInputValueChange: (value: string) => void;
  textareaRef: React.RefObject<HTMLTextAreaElement>;
  selectedFiles: File[];
  onRemoveFile: (index: number) => void;
  referenceBlocks?: Array<{ id: string; text: string }>;
  onRemoveReferenceBlock?: (id: string) => void;
  inputActions?: Array<{ id: string; label: string; icon?: "deep-think" }>;
  selectedActionIds?: Set<string>;
  onToggleAction?: (actionId: string) => void;
  showUpload: boolean;
  uploadAccept?: string;
  onFileChange: (event: React.ChangeEvent<HTMLInputElement>) => void;
  fileInputRef: React.RefObject<HTMLInputElement>;
  canSend: boolean;
  isSending: boolean;
  onSend: () => void;
  onStop: () => void;
  onInputKeyDown: (event: React.KeyboardEvent<HTMLTextAreaElement>) => void;
}

const formatFileSize = (size: number) => {
  if (!Number.isFinite(size) || size <= 0) return "0KB";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const digits = value >= 100 || unitIndex === 0 ? 0 : 1;
  return `${value.toFixed(digits)}${units[unitIndex]}`;
};

const getFileExtension = (name: string) => {
  const parts = name.split(".");
  return parts.length > 1 ? parts[parts.length - 1].toLowerCase() : "";
};

const FileCard = ({ file, onRemove }: { file: File; onRemove: () => void }) => {
  const extension = getFileExtension(file.name);
  const isImage = file.type.startsWith("image/") || ["png", "jpg", "jpeg", "gif", "webp"].includes(extension);
  const [previewUrl, setPreviewUrl] = useState("");

  useEffect(() => {
    if (!isImage) {
      setPreviewUrl("");
      return undefined;
    }
    const url = URL.createObjectURL(file);
    setPreviewUrl(url);
    return () => {
      URL.revokeObjectURL(url);
    };
  }, [file, isImage]);

  const iconClass =
    extension === "pdf"
      ? "fa-file-pdf text-[#E65054]"
      : ["doc", "docx"].includes(extension)
        ? "fa-file-word text-[#2B579A]"
        : ["xls", "xlsx"].includes(extension)
          ? "fa-file-excel text-[#217346]"
          : extension === "ppt" || extension === "pptx"
            ? "fa-file-powerpoint text-[#D04423]"
            : "fa-file-lines text-[#6B7280]";

  return (
    <div className="relative h-[76px] w-[210px] rounded-[12px] border border-[#E2E6EA] bg-white shadow-[0_1px_2px_rgba(0,0,0,0.06)]">
      <button
        type="button"
        className="absolute -right-2 -top-2 inline-flex h-5 w-5 items-center justify-center rounded-full bg-[#C2CAD4] text-white shadow"
        onClick={onRemove}
        aria-label="移除附件"
      >
        <i className="fa-solid fa-xmark text-[10px]"></i>
      </button>
      {isImage ? (
        <img src={previewUrl} alt={file.name} className="h-full w-full rounded-[12px] object-cover" />
      ) : (
        <div className="flex h-full items-center gap-2 px-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-[8px] bg-[#F6F8FA]">
            <i className={`fa-solid ${iconClass} text-lg`}></i>
          </div>
          <div className="min-w-0">
            <div className="truncate text-[13px] font-[500] text-[#1F3A56]" title={file.name}>
              {file.name}
            </div>
            <div className="text-[12px] text-[#8A93A3]">
              {(extension || "文件").toUpperCase()}, {formatFileSize(file.size)}
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export function Composer({
  footerTagText,
  inputValue,
  placeholderText,
  onInputValueChange,
  textareaRef,
  selectedFiles,
  onRemoveFile,
  referenceBlocks = [],
  onRemoveReferenceBlock,
  inputActions,
  selectedActionIds,
  onToggleAction,
  showUpload,
  uploadAccept,
  onFileChange,
  fileInputRef,
  canSend,
  isSending,
  onSend,
  onStop,
  onInputKeyDown,
}: ComposerProps) {
  return (
    <footer className="pt-6">
      <div className="flex min-h-[120px] w-full flex-col gap-3 rounded-[20px] border border-slate-100 bg-white px-6 py-3 shadow-[0_4px_25px_1px_rgba(0,0,0,0.1)] transition-all duration-150 focus-within:border-[#7BB7D4] focus-within:shadow-[0_0_0_3px_rgba(0,126,167,0.12)]">
        {referenceBlocks.length > 0 ? (
          <div className="flex flex-col gap-2 text-[13px] text-[#1F3A56]">
            {referenceBlocks.map((item) => (
              <div
                key={item.id}
                className="flex items-start justify-between gap-3 rounded-[12px] border border-[#E5EAF0] bg-[#F6F8FA] px-4 py-2"
              >
                <div className="flex-1 truncate leading-[20px]" title={item.text}>
                  {item.text}
                </div>
                <button
                  type="button"
                  className="mt-0.5 inline-flex h-5 w-5 items-center justify-center rounded-full text-[#8592A6] hover:bg-[#EEF2F5]"
                  onClick={() => onRemoveReferenceBlock?.(item.id)}
                  aria-label="移除引用"
                >
                  <i className="fa-solid fa-xmark text-[10px]"></i>
                </button>
              </div>
            ))}
          </div>
        ) : null}
        <textarea
          ref={textareaRef}
          rows={3}
          placeholder={placeholderText}
          value={inputValue}
          onChange={(event) => onInputValueChange(event.target.value)}
          onKeyDown={onInputKeyDown}
          className="w-full flex-1 min-h-[40px] resize-none border-0 text-[14px] leading-[24px] text-black outline-none placeholder:text-[16px] placeholder:font-[500] placeholder:leading-[24px] placeholder:text-[#0000003F] placeholder:opacity-100"
        />
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2 text-[14px] font-[500] leading-[22px] text-[rgba(133,146,166,1)]">
            {(inputActions ?? []).map((action) => {
              const isSelected = selectedActionIds?.has(action.id);
              const isLocked = Boolean(action.defaultSelected);
              return (
                <button
                  key={action.id}
                  type="button"
                  disabled={isLocked}
                  className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 transition-colors ${
                    isSelected ? "bg-cyan-100 dark:bg-cyan-900/50" : "bg-slate-100"
                  } enabled:cursor-pointer disabled:cursor-not-allowed disabled:opacity-60`}
                  onClick={() => onToggleAction?.(action.id)}
                >
                  <span
                    style={
                      isSelected
                        ? {
                            background: "linear-gradient(112deg, #007EA7 1%, #1F3A56 100%)",
                            WebkitBackgroundClip: "text",
                            WebkitTextFillColor: "transparent",
                            backgroundClip: "text",
                            display: "flex",
                            alignItems: "center",
                            gap: "4px",
                          }
                        : { color: "#6B7280", display: "flex", alignItems: "center", gap: "4px" }
                    }
                  >
                    {action.icon === "deep-think" && <DeepThinkIcon className="h-4 w-4" />}
                    {action.label}
                  </span>
                </button>
              );
            })}
          </div>
          <div className="flex items-center gap-3">
            {showUpload ? (
              <button type="button" className="relative h-10 w-10" onClick={() => fileInputRef.current?.click()}>
                <img
                  src={uploadIcon}
                  alt="上传"
                  className="absolute left-[3.84px] top-[2.73px] h-[79.46%] w-[73.36%] opacity-100"
                />
              </button>
            ) : null}
            {showUpload ? (
              <input
                ref={fileInputRef}
                type="file"
                className="hidden"
                multiple
                accept={uploadAccept}
                onChange={onFileChange}
              />
            ) : null}
            <button
              type="button"
              className="relative flex h-10 w-10 items-center justify-center"
              disabled={!isSending && !canSend}
              onClick={isSending ? onStop : onSend}
            >
              <img
                src={isSending ? stopOkIcon : canSend ? sendOkIcon : sendIcon}
                alt={isSending ? "停止" : "发送"}
                className="absolute left-0 top-0 h-10 w-10 opacity-50"
              />
            </button>
          </div>
        </div>
        {selectedFiles.length > 0 && (
          <div className="rounded-[16px] bg-[#F5F6F7] px-3 py-3">
            <div className="flex flex-wrap items-center gap-3">
              {selectedFiles.map((file, index) => (
                <FileCard key={`${file.name}-${index}`} file={file} onRemove={() => onRemoveFile(index)} />
              ))}
            </div>
          </div>
        )}
      </div>
      <div className="flex h-[30px] items-center justify-center gap-4 text-[14px] text-slate-400">
        <span className="text-[rgba(0,126,167,1)]">{footerTagText}</span>
        <span>内容由AI生成，请仔细甄别</span>
      </div>
    </footer>
  );
}
