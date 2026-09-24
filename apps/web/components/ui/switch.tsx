"use client";

import { Switch as RadixSwitch } from "radix-ui";
import { forwardRef, useId } from "react";

import { cn } from "@/lib/utils";

export interface SwitchProps extends Omit<React.ComponentProps<typeof RadixSwitch.Root>, "children" | "ref"> {
  /** Visible label. Omit only when the control is labelled by `aria-label`/`aria-labelledby` or a Field. */
  label?: React.ReactNode;
  /** Classes for the label wrapper (only rendered with `label`). */
  labelClassName?: string;
}

/** An on/off toggle that applies immediately (spec §73). Use Checkbox for choices submitted with a form. */
export const Switch = forwardRef<HTMLButtonElement, SwitchProps>(function Switch(
  { label, className, labelClassName, id, ...props },
  ref,
) {
  const autoId = useId();
  const controlId = id ?? autoId;
  const control = (
    <RadixSwitch.Root
      ref={ref}
      id={controlId}
      className={cn(
        "peer relative inline-flex h-5 w-9 shrink-0 cursor-pointer items-center rounded-full border transition-colors",
        "border-control bg-surface-2 data-[state=checked]:border-accent-strong data-[state=checked]:bg-accent",
        "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
        "disabled:cursor-not-allowed disabled:opacity-50",
        className,
      )}
      {...props}
    >
      <RadixSwitch.Thumb
        className={cn(
          "pointer-events-none block size-3.5 rounded-full bg-control shadow-subtle transition-transform",
          "translate-x-0.5 data-[state=checked]:translate-x-4.5 data-[state=checked]:bg-on-accent",
        )}
      />
    </RadixSwitch.Root>
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
