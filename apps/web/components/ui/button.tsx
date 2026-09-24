import { Loader2 } from "lucide-react";
import { Slot } from "radix-ui";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger" | "ai";
export type ButtonSize = "sm" | "md";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "bg-accent text-on-accent border border-accent-strong hover:bg-accent-strong",
  secondary: "bg-surface text-fg border border-strong hover:bg-surface-2",
  ghost: "bg-transparent text-fg-secondary border border-transparent hover:bg-surface-2 hover:text-fg",
  danger: "bg-surface text-danger-fg border border-strong hover:bg-danger-soft hover:border-danger",
  // AI actions: mint outline, never a gradient (spec §102)
  ai: "bg-accent-soft text-accent-fg border border-accent hover:border-accent-strong",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-7 px-2.5 gap-1.5 text-xs",
  md: "h-8 px-3 gap-2 text-sm",
};

export interface ButtonProps extends React.ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  loading?: boolean;
  asChild?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  {
    variant = "secondary",
    size = "md",
    loading = false,
    asChild = false,
    className,
    children,
    disabled,
    ...props
  },
  ref,
) {
  const classes = cn(
    "inline-flex shrink-0 items-center justify-center rounded-sm font-medium whitespace-nowrap",
    "transition-colors disabled:pointer-events-none disabled:opacity-50",
    VARIANTS[variant],
    SIZES[size],
    className,
  );
  if (asChild) {
    return (
      <Slot.Root ref={ref} className={classes} {...props}>
        {children}
      </Slot.Root>
    );
  }
  return (
    <button
      ref={ref}
      className={classes}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...props}
    >
      {loading ? <Loader2 aria-hidden className="size-3.5 animate-spin" /> : null}
      {children}
    </button>
  );
});
