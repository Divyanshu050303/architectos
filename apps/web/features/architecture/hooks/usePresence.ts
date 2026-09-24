/**
 * Keeps a panel mounted for its exit animation (spec §100): `state` drives the CSS
 * (`data-state="open" | "closed"`); under reduced motion it unmounts at once.
 */
import { useEffect, useState } from "react";

import { useMediaQuery } from "./useMediaQuery";

export function usePresence(open: boolean, exitMs: number): { mounted: boolean; state: "open" | "closed" } {
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)", true);
  const [mounted, setMounted] = useState(open);
  if (open && !mounted) setMounted(true);

  useEffect(() => {
    if (open || !mounted) return;
    const timer = setTimeout(() => setMounted(false), reducedMotion ? 0 : exitMs);
    return () => clearTimeout(timer);
  }, [open, mounted, reducedMotion, exitMs]);

  return { mounted: open || mounted, state: open ? "open" : "closed" };
}
