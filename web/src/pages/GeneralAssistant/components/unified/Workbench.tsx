import type { Agent } from "../../types";
import { ComplaintWorkbench } from "./workbench/ComplaintWorkbench";
import { ExpertWorkbench } from "./workbench/ExpertWorkbench";

interface WorkbenchProps {
  workbench?: Agent["workbench"];
  isVisible: boolean;
  activePanelId: string | null;
  onPanelChange: (panelId: string) => void;
  complaintFiles?: Array<{ order: number; name: string; url: string }>;
  complaintSelectedIndex?: number;
  onComplaintSelectIndex?: (index: number) => void;
  complaintRawTextFiles?: Array<{ order: number; name: string; pages: Array<{ page: number; content: string }> }>;
  onAskFromRawText?: (text: string) => void;
  expertCitation?: import("@/pages/AISpecialistHome/types").ParsedCitation | null;
  onClose: () => void;
  expertConversationId?: string;
  expertTraceEnabled?: boolean;
}

export function Workbench({
  workbench,
  isVisible,
  activePanelId,
  onPanelChange,
  complaintFiles,
  complaintSelectedIndex,
  onComplaintSelectIndex,
  complaintRawTextFiles,
  onAskFromRawText,
  expertCitation,
  onClose,
  expertConversationId,
  expertTraceEnabled,
}: WorkbenchProps) {
  if (!workbench?.enabled || !isVisible) return null;

  return (
    <div className="w-full flex-none self-stretch">
      <div className="sticky flex h-[calc(100vh-68px)] flex-col rounded-[20px] border border-[#EEF2F5] bg-white p-5 shadow-[0_6px_24px_rgba(15,23,42,0.08)]">
        {workbench.variant === "complaint" ? (
          <ComplaintWorkbench
            activePanelId={activePanelId}
            onPanelChange={onPanelChange}
            serverFiles={complaintFiles}
            selectedFileIndex={complaintSelectedIndex}
            onSelectFileIndex={onComplaintSelectIndex}
            rawTextFiles={complaintRawTextFiles}
            onAskFromRawText={onAskFromRawText}
            onClose={onClose}
          />
        ) : (
          <ExpertWorkbench
            activePanelId={activePanelId}
            onPanelChange={onPanelChange}
            citation={expertCitation ?? null}
            onClose={onClose}
            conversationId={expertConversationId}
            traceEnabled={expertTraceEnabled}
          />
        )}
      </div>
    </div>
  );
}
