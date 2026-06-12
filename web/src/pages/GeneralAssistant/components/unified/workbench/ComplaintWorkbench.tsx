import { ImagePreview } from "@/components/ImagePreview";
import { PDFPreview } from "@/components/PDFPreview";
import { EmptyState } from "@/components/EmptyState";
import { toast } from "sonner";

const PANELS = [
  { id: "original", label: "原件" },
  { id: "text", label: "原文" },
];

interface ComplaintWorkbenchProps {
  activePanelId: string | null;
  onPanelChange: (panelId: string) => void;
  serverFiles?: Array<{ order: number; name: string; url: string }>;
  selectedFileIndex?: number;
  onSelectFileIndex?: (index: number) => void;
  rawTextFiles?: Array<{ order: number; name: string; pages: Array<{ page: number; content: string }> }>;
  onAskFromRawText?: (text: string) => void;
  onClose: () => void;
}

export function ComplaintWorkbench({
  activePanelId,
  onPanelChange,
  serverFiles = [],
  selectedFileIndex = 0,
  onSelectFileIndex,
  rawTextFiles = [],
  onAskFromRawText,
  onClose,
}: ComplaintWorkbenchProps) {
  const currentId = activePanelId || PANELS[0].id;
  const activeFile = serverFiles[selectedFileIndex];
  const activeRawText = rawTextFiles.find(
    (file) => file.order === (activeFile?.order ?? 0)
  );
  const activeFileName = activeFile?.name || activeFile?.url || "";
  const isImageFile = /\.(png|jpe?g|gif|webp|bmp)$/i.test(activeFileName);
  const buildParagraphs = (content: string) =>
    content
      .split(/\n{2,}/)
      .map((paragraph) => paragraph.trim())
      .filter(Boolean);

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-3 text-[14px] text-[#8592A6]">
          {serverFiles.length > 0 ? (
            <div className="flex items-center gap-2 text-[12px] text-[#1F3A56]">
              <span className="text-[#8592A6]">文档：</span>
              {serverFiles.length > 1 ? (
                <select
                  value={selectedFileIndex}
                  onChange={(event) => onSelectFileIndex?.(Number(event.target.value))}
                  className="min-w-[180px] rounded-[10px] border border-[#E5EAF0] bg-white px-3 py-1 text-[12px] text-[#1F3A56]"
                >
                  {serverFiles.map((file, index) => (
                    <option key={`${file.order}-${file.name}-${index}`} value={index}>
                      {`${file.order}. ${file.name}`}
                    </option>
                  ))}
                </select>
              ) : (
                <span>{serverFiles[0].name}</span>
              )}
            </div>
          ) : null}
          {PANELS.map((panel) => {
            const isActive = panel.id === currentId;
            return (
              <button
                key={panel.id}
                type="button"
                onClick={() => onPanelChange(panel.id)}
                className={
                  isActive
                    ? "rounded-full bg-[#EAF2F6] px-3 py-1 text-[20px] font-[500] leading-[28px] text-[#007EA7]"
                    : "px-2 text-[14px] text-[#8592A6]"
                }
              >
                {panel.label}
              </button>
            );
          })}
        </div>
        <button type="button" className="text-[#A3AAB5]" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="mt-4 flex min-h-0 flex-1 flex-col gap-4">
        <div
          className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-[16px] bg-[#F7F9FA] p-4"
          data-preview-wrap
          style={{ display: currentId === "original" ? "flex" : "none" }}
        >
          {activeFile?.url ? (
            isImageFile ? (
              <ImagePreview fileUrl={activeFile.url} fileName={activeFile.name} />
            ) : (
              <PDFPreview fileUrl={activeFile.url} fileName={activeFile.name} />
            )
          ) : (
            <EmptyState
              title="暂无举报件预览"
              description="上传 PDF 或图片后将自动转换为可预览的长文档"
              className="mt-0 h-full"
            />
          )}
        </div>
        <div
          className="flex-1 overflow-y-auto rounded-[16px] bg-[#F7F9FA] p-4 text-[14px] leading-[24px] text-[#1F3A56]"
          style={{ display: currentId === "text" ? "block" : "none" }}
        >
          {activeRawText?.pages?.length ? (
            <div className="flex flex-col gap-4">
              {activeRawText.pages.map((page, index) => (
                <div key={`${activeRawText.order}-${page.page}-${index}`}>
                  <div className="flex flex-col gap-2">
                    {buildParagraphs(page.content).map((paragraph, paragraphIndex) => (
                      <div
                        key={`${activeRawText.order}-${page.page}-${paragraphIndex}`}
                        className="group relative rounded-[10px] px-3 py-2 transition-colors hover:bg-white"
                      >
                        <div className="whitespace-pre-wrap select-text">{paragraph}</div>
                        <div className="pointer-events-none absolute right-3 top-2 flex translate-y-1 items-center gap-1 rounded-full border border-[#E5EAF0] bg-white px-2 py-1 text-[12px] text-[#1F3A56] opacity-0 shadow-sm transition-all group-hover:pointer-events-auto group-hover:translate-y-0 group-hover:opacity-100">
                          <button
                            type="button"
                            className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[#5A6B7B] hover:text-[#007EA7]"
                            onClick={() => {
                              navigator.clipboard
                                .writeText(paragraph)
                                .then(() => toast.success("已复制"))
                                .catch(() => toast.error("复制失败"));
                            }}
                          >
                            <i className="fa-regular fa-copy text-[11px]"></i>
                            复制
                          </button>
                          <span className="h-3 w-px bg-[#E5EAF0]" />
                          <button
                            type="button"
                            className="flex items-center gap-1 rounded-full px-2 py-0.5 text-[#5A6B7B] hover:text-[#007EA7]"
                            onClick={() => onAskFromRawText?.(paragraph)}
                          >
                            <i className="fa-regular fa-comment-dots text-[11px]"></i>
                            问一问
                          </button>
                        </div>
                      </div>
                    ))}
                  </div>
                  {index < activeRawText.pages.length - 1 ? (
                    <div className="mt-4 h-px w-full bg-[#E5EAF0]" />
                  ) : null}
                </div>
              ))}
            </div>
          ) : (
            <EmptyState title="暂无举报件原文" description="完成 OCR 或文本抽取后显示在此处" className="mt-0" />
          )}
        </div>
      </div>
    </div>
  );
}
