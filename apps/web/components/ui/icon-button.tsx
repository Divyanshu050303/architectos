import { forwardRef } from "react";

import { cn } from "@/lib/utils";

import { Tooltip } from "./tooltip";

export interface IconButtonProps extends Omit<React.ButtonHTMLAttributes<HTMLButtonElement>, "aria-label"> {
  /** Accessible name; also shown as the tooltip. */
  label: string;
  /** Keyboard shortcut shown in the tooltip, e.g. "mod+z" (spec §60). */
  shortcut?: string;
  active?: boolean;
  size?: "sm" | "md";
}

export const IconButton = forwardRef<HTMLButtonElement, IconButtonProps>(function IconButton(
  { label, shortcut, active = false, size = "md", className, children, ...props },
  ref,
) {
  return (
    <Tooltip content={label} shortcut={shortcut}>
      <button
        ref={ref}
        type="button"
        aria-label={label}
        data-active={active || undefined}
        className={cn(
          "inline-flex shrink-0 items-center justify-center rounded-sm text-fg-secondary transition-colors",
          "hover:bg-surface-2 hover:text-fg disabled:pointer-events-none disabled:opacity-40",
          "data-[active]:bg-accent-soft data-[active]:text-accent-fg",
          size === "sm" ? "size-7 [&_svg]:size-3.5" : "size-8 [&_svg]:size-4",
          className,
        )}
        {...props}
      >
        {children}
      </button>
    </Tooltip>
  );
});
