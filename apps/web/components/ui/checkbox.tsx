"use client";

import { Check, Minus } from "lucide-react";
import { Checkbox as RadixCheckbox } from "radix-ui";
import { forwardRef, useId } from "react";

import { cn } from "@/lib/utils";

export type CheckboxSize = "sm" | "md";

const BOX: Record<CheckboxSize, string> = { sm: "size-3.5", md: "size-4" };
const ICON: Record<CheckboxSize, string> = { sm: "size-2.5", md: "size-3" };

export interface CheckboxProps extends Omit<
  React.ComponentProps<typeof RadixCheckbox.Root>,
  "children" | "ref"
> {
  /** Visible label. Omit only when the control is labelled by `aria-label`/`aria-labelledby` or a Field. */
  label?: React.ReactNode;
  size?: CheckboxSize;
  /** Classes for the label wrapper (only rendered with `label`). */
  labelClassName?: string;
}

/** A tri-state checkbox (spec §73). Radix renders a `role="checkbox"` button; the label is a real `<label>`. */
export const Checkbox = forwardRef<HTMLButtonElement, CheckboxProps>(function Checkbox(
  { label, size = "md", className, labelClassName, id, ...props },
  ref,
) {
  const autoId = useId();
  const controlId = id ?? autoId;
  const control = (
    <RadixCheckbox.Root
      ref={ref}
      id={controlId}
      className={cn(
        "peer inline-flex shrink-0 items-center justify-center rounded-sm border border-control bg-surface text-on-accent",
        "transition-colors hover:border-accent-strong disabled:cursor-not-allowed disabled:opacity-50",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "data-[state=checked]:border-accent-strong data-[state=checked]:bg-accent-strong",
        "data-[state=indeterminate]:border-accent-strong data-[state=indeterminate]:bg-accent-strong",
        "aria-[invalid=true]:border-danger",
        BOX[size],
        className,
      )}
      {...props}
    >
      <RadixCheckbox.Indicator className="group flex items-center justify-center">
        <Check
          aria-hidden
          strokeWidth={3}
          className={cn(ICON[size], "group-data-[state=indeterminate]:hidden")}
        />
        <Minus
          aria-hidden
          strokeWidth={3}
          className={cn(ICON[size], "hidden group-data-[state=indeterminate]:block")}
        />
      </RadixCheckbox.Indicator>
    </RadixCheckbox.Root>
  );
  if (label === undefined) return control;
  return (
    <span className={cn("inline-flex items-center gap-2", labelClassName)}>
      {control}
      <label
        htmlFor={controlId}
        className="cursor-pointer peer-disabled:cursor-not-allowed peer-disabled:opacity-50"
      >
        {label}
      </label>
    </span>
  );
});
