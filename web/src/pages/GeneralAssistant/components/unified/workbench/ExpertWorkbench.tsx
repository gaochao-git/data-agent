import PolicyPreviewPanel from "@/pages/AISpecialistHome/RightPanels/PolicyPreviewPanel";
import TracePanel from "@/pages/AISpecialistHome/RightPanels/TracePanel";
import type { ParsedCitation } from "@/pages/AISpecialistHome/types";

interface ExpertWorkbenchProps {
  activePanelId: string | null;
  onPanelChange: (panelId: string) => void;
  citation: ParsedCitation | null;
  onClose: () => void;
  conversationId?: string;
  traceEnabled?: boolean;
}

export function ExpertWorkbench({
  activePanelId,
  onPanelChange,
  citation,
  onClose,
  conversationId,
  traceEnabled = false,
}: ExpertWorkbenchProps) {
  const panels = [
    { id: "reference", label: "法规依据" },
    ...(traceEnabled ? [{ id: "trace", label: "调试" }] : []),
  ];
  const currentId = activePanelId || panels[0].id;
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-[14px] text-[#8592A6]">
          {panels.map((panel) => (
            <button
              key={panel.id}
              type="button"
              onClick={() => onPanelChange(panel.id)}
              className={
                panel.id === currentId
                  ? "rounded-full bg-[#EAF2F6] px-3 py-1 text-[20px] font-[500] leading-[28px] text-[#007EA7]"
                  : "px-2 text-[14px] text-[#8592A6]"
              }
            >
              {panel.label}
            </button>
          ))}
        </div>
        <button type="button" className="text-[#A3AAB5]" onClick={onClose}>
          ✕
        </button>
      </div>
      <div className="mt-4 flex-1 overflow-hidden rounded-[16px] bg-[#F7F9FA]">
        {currentId === "trace" && traceEnabled ? (
          <TracePanel conversationId={conversationId ?? null} />
        ) : (
          <PolicyPreviewPanel citation={citation} />
        )}
      </div>
    </div>
  );
}
