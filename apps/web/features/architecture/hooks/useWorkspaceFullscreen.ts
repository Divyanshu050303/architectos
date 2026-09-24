/**
 * Fullscreen: browser fullscreen plus a fixed "focus" layout, so portalled dialogs,
 * menus and toasts stay visible.
 */
import { useCallback, useEffect } from "react";

import { useWorkspaceStore } from "@/stores/workspace-store";

export function useWorkspaceFullscreen(): () => void {
  useEffect(() => {
    const onChange = () => useWorkspaceStore.getState().setFullscreen(document.fullscreenElement !== null);
    document.addEventListener("fullscreenchange", onChange);
    return () => {
      document.removeEventListener("fullscreenchange", onChange);
      useWorkspaceStore.getState().setFullscreen(false);
    };
  }, []);
  return useCallback(() => {
    if (document.fullscreenElement) void document.exitFullscreen();
    else void document.documentElement.requestFullscreen?.().catch(() => undefined);
  }, []);
}
