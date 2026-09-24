"use client";

import { useState } from "react";

import { cn } from "@/lib/utils";

export interface AppearListItemProps extends React.LiHTMLAttributes<HTMLLIElement> {
  /** Position in the list, used to stagger the entrance (capped in CSS). */
  index: number;
}

/**
 * A list item that plays `.motion-appear` (styles/architecture.css) once, when it first mounts:
 * a finding appearing (spec §100). The stagger index is frozen at mount, so re-renders, reorders
 * or new siblings never change its animation-delay and replay it. Key the item by a stable id.
 * The animation is defined only under `prefers-reduced-motion: no-preference`.
 */
export function AppearListItem({ index, className, style, ...props }: AppearListItemProps) {
  const [appearIndex] = useState(index);
  return (
    <li
      className={cn("motion-appear", className)}
      style={{ ...style, "--motion-index": appearIndex } as React.CSSProperties}
      {...props}
    />
  );
}
