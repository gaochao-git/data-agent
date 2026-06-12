import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import type { RefObject } from "react";

const LEFT_SIDEBAR_STORAGE_KEY = "aiHome.leftSidebarState";
const EXPAND_BUTTON_SIZE = 44;

const getStoredLeftSidebarState = () => {
  if (typeof window === "undefined") {
    return true;
  }
  const stored = window.localStorage.getItem(LEFT_SIDEBAR_STORAGE_KEY);
  if (stored === "expanded") {
    return false;
  }
  if (stored === "collapsed") {
    return true;
  }
  return true;
};

export function useSidebarCollapse(layoutContainerRef: RefObject<HTMLDivElement>) {
  const [isLeftCollapsed, setIsLeftCollapsed] = useState<boolean>(() =>
    getStoredLeftSidebarState(),
  );
  const leftSidebarPreferenceRef = useRef<boolean>(isLeftCollapsed);
  const leftSidebarStateRef = useRef<boolean>(isLeftCollapsed);
  const collapseSidebarButtonRef = useRef<HTMLButtonElement | null>(null);
  const expandSidebarButtonRef = useRef<HTMLButtonElement | null>(null);
  const [expandButtonTop, setExpandButtonTop] = useState<number | null>(null);

  const persistLeftSidebarPreference = useCallback((collapsed: boolean) => {
    leftSidebarPreferenceRef.current = collapsed;
    if (typeof window !== "undefined") {
      window.localStorage.setItem(
        LEFT_SIDEBAR_STORAGE_KEY,
        collapsed ? "collapsed" : "expanded",
      );
    }
  }, []);

  const toggleLeftSidebar = useCallback(() => {
    setIsLeftCollapsed((prev) => {
      const next = !prev;
      persistLeftSidebarPreference(next);
      return next;
    });
  }, [persistLeftSidebarPreference]);

  const syncExpandButtonPosition = useCallback(() => {
    if (
      typeof window === "undefined" ||
      !layoutContainerRef.current ||
      !collapseSidebarButtonRef.current
    ) {
      return;
    }
    const containerRect = layoutContainerRef.current.getBoundingClientRect();
    const collapseRect = collapseSidebarButtonRef.current.getBoundingClientRect();
    const collapseCenter =
      collapseRect.top + collapseRect.height / 2 - containerRect.top;
    const expandHeight =
      expandSidebarButtonRef.current?.offsetHeight ?? EXPAND_BUTTON_SIZE;
    const computedTop = collapseCenter - expandHeight / 2;
    setExpandButtonTop(Math.max(8, computedTop));
  }, [layoutContainerRef]);

  useLayoutEffect(() => {
    if (!isLeftCollapsed) {
      setExpandButtonTop(null);
      return;
    }
    syncExpandButtonPosition();
    window.addEventListener("resize", syncExpandButtonPosition);
    return () => {
      window.removeEventListener("resize", syncExpandButtonPosition);
    };
  }, [isLeftCollapsed, syncExpandButtonPosition]);

  useEffect(() => {
    leftSidebarStateRef.current = isLeftCollapsed;
  }, [isLeftCollapsed]);

  return {
    isLeftCollapsed,
    setIsLeftCollapsed,
    leftSidebarPreferenceRef,
    leftSidebarStateRef,
    collapseSidebarButtonRef,
    expandSidebarButtonRef,
    expandButtonTop,
    toggleLeftSidebar,
  };
}
