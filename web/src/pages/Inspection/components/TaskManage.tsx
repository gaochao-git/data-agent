import Lottie from "lottie-react";
import thinkingAnimation from "@/assets/thinking.json";
import { mergeClassNames } from "../utils";
import type { AnalysisTask } from "../types";

function ThinkingIcon({ size = 20 }: { size?: number }) {
  return (
    <div style={{ width: size, height: size }}>
      <Lottie
        animationData={thinkingAnimation}
        loop
        rendererSettings={{ preserveAspectRatio: "xMidYMid slice" }}
        style={{ width: "100%", height: "100%" }}
      />
    </div>
  );
}

interface TaskManageProps {
  tasks: AnalysisTask[];
}

export function TaskManage({ tasks }: TaskManageProps) {
  if (!tasks || tasks.length === 0) {
    return null;
  }

  return (
    <div className="flex flex-col gap-2">
      {tasks.map((task) => {
        const isCompleted = task.status === "completed";
        const isInProgress = task.status === "in_progress";
        const isPending = task.status === "pending";
        const isCancelled = task.status === "cancelled";
        const isError = task.status === "error";
        const isAwaitingConfirm = task.status === "awaiting_confirm";

        return (
          <div
            key={task.id}
            className={mergeClassNames(
              "flex items-center gap-2 px-3 py-2 text-sm transition-colors rounded-lg",
              isAwaitingConfirm
                ? "bg-amber-50 dark:bg-amber-900/20"
                : "bg-gray-50 dark:bg-gray-800/50"
            )}
          >
            {/* 状态图标 */}
            <div className="flex-shrink-0">
              {isCompleted && (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-emerald-500 text-white">
                  <i className="fa-solid fa-check text-xs"></i>
                </div>
              )}
              {isInProgress && (
                <ThinkingIcon size={20} />
              )}
              {isPending && (
                <div className="h-5 w-5 rounded-full border-2 border-gray-300 dark:border-gray-600"></div>
              )}
              {isCancelled && (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-gray-500 text-white">
                  <i className="fa-solid fa-ban text-xs"></i>
                </div>
              )}
              {isError && (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-rose-500 text-white">
                  <i className="fa-solid fa-xmark text-xs"></i>
                </div>
              )}
              {isAwaitingConfirm && (
                <div className="flex h-5 w-5 items-center justify-center rounded-full bg-amber-500 text-white">
                  <i className="fa-solid fa-user-check text-xs"></i>
                </div>
              )}
            </div>

            {/* 任务标题 */}
            <div className="flex-1 font-medium text-gray-900 dark:text-gray-100">
              {task.title}
            </div>
          </div>
        );
      })}
    </div>
  );
}
