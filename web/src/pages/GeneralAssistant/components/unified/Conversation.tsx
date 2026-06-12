import type { AgentOpeningMessage } from "../../types";
import type { ChatMessage } from "../UnifiedEmbeddedContent";
import { ConversationMessages } from "./conversation/ConversationMessages";
import { Composer } from "./conversation/Composer";

interface ConversationProps {
  assistantRole: string;
  footerTagText: string;
  openingMessage?: AgentOpeningMessage;
  showOpening: boolean;
  chatMessages: ChatMessage[];
  scrollContainerRef: React.RefObject<HTMLElement>;
  onScroll: () => void;
  inputValue: string;
  placeholderText: string;
  onInputValueChange: (value: string) => void;
  onPromptClick: (prompt: string) => void;
  onUploadClick: () => void;
  onFocusInput: () => void;
  onReferenceClick?: (ref: ChatMessage["references"][number]) => void;
  onAttachmentClick?: (attachment: ChatMessage["attachment"]) => void;
  onSuggestionClick?: (suggestion: NonNullable<ChatMessage["suggestions"]>[number]) => void;
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

export function Conversation({
  assistantRole,
  footerTagText,
  openingMessage,
  showOpening,
  chatMessages,
  scrollContainerRef,
  onScroll,
  inputValue,
  placeholderText,
  onInputValueChange,
  onPromptClick,
  onUploadClick,
  onFocusInput,
  onReferenceClick,
  onAttachmentClick,
  onSuggestionClick,
  textareaRef,
  selectedFiles,
  onRemoveFile,
  referenceBlocks,
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
}: ConversationProps) {
  return (
    <div className="flex min-h-0 w-full max-w-[1063px] flex-1 flex-col self-stretch">
      <ConversationMessages
        assistantRole={assistantRole}
        openingMessage={openingMessage}
        showOpening={showOpening}
        chatMessages={chatMessages}
        scrollContainerRef={scrollContainerRef}
        onScroll={onScroll}
        onPromptClick={onPromptClick}
        onUploadClick={onUploadClick}
        onFocusInput={onFocusInput}
        onReferenceClick={onReferenceClick}
        onAttachmentClick={onAttachmentClick}
        onSuggestionClick={onSuggestionClick}
      />
      <Composer
        footerTagText={footerTagText}
        inputValue={inputValue}
        placeholderText={placeholderText}
        onInputValueChange={onInputValueChange}
        textareaRef={textareaRef}
        selectedFiles={selectedFiles}
        onRemoveFile={onRemoveFile}
        referenceBlocks={referenceBlocks}
        onRemoveReferenceBlock={onRemoveReferenceBlock}
        inputActions={inputActions}
        selectedActionIds={selectedActionIds}
        onToggleAction={onToggleAction}
        showUpload={showUpload}
        uploadAccept={uploadAccept}
        onFileChange={onFileChange}
        fileInputRef={fileInputRef}
        canSend={canSend}
        isSending={isSending}
        onSend={onSend}
        onStop={onStop}
        onInputKeyDown={onInputKeyDown}
      />
    </div>
  );
}
