import type { ReactNode } from "react";
import { cn } from "@/lib/utils";

interface EmptyStateProps {
  title: string;
  description?: string;
  icon?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, icon, className }: EmptyStateProps) {
  return (
    <div
      className={cn(
        "mt-4 flex flex-col items-center justify-center gap-3 rounded-2xl border border-gray-200 bg-gray-50 px-6 py-10 text-center text-gray-500",
        className
      )}
    >
      {icon ? <div className="text-2xl text-gray-400">{icon}</div> : null}
      <p className="text-base font-medium text-gray-900">{title}</p>
      {description ? <p className="text-sm text-gray-500">{description}</p> : null}
    </div>
  );
}
