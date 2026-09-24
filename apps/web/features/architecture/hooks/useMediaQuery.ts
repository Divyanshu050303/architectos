import { useCallback, useSyncExternalStore } from "react";

/** Matches a media query; `serverValue` is used during SSR and hydration. */
export function useMediaQuery(query: string, serverValue = true): boolean {
  const subscribe = useCallback(
    (onChange: () => void) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    [query],
  );
  return useSyncExternalStore(
    subscribe,
    () => window.matchMedia(query).matches,
    () => serverValue,
  );
}

/** Canvas editing is desktop-only; smaller screens get a read-only canvas (spec §63). */
export function useCanvasEditable(): boolean {
  return useMediaQuery("(min-width: 1024px)");
}
