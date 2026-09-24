"use client";

import { Slider as RadixSlider } from "radix-ui";
import { forwardRef } from "react";

import { cn } from "@/lib/utils";

export type SliderTone = "accent" | "danger";

const RANGE: Record<SliderTone, string> = { accent: "bg-accent-strong", danger: "bg-danger" };
const THUMB: Record<SliderTone, string> = { accent: "border-accent-strong", danger: "border-danger" };

export interface SliderProps extends Omit<
  React.ComponentProps<typeof RadixSlider.Root>,
  "value" | "defaultValue" | "onValueChange" | "onValueCommit" | "children" | "ref"
> {
  value?: number;
  defaultValue?: number;
  onValueChange?: (value: number) => void;
  onValueCommit?: (value: number) => void;
  /** Accessible name when there is no visible label (otherwise use `aria-labelledby`). */
  "aria-label"?: string;
  "aria-labelledby"?: string;
  "aria-describedby"?: string;
  /** Human-readable value for screen readers, e.g. "1.2M daily active users". */
  "aria-valuetext"?: string;
  tone?: SliderTone;
}

/**
 * A single-value range control (spec §73). The ARIA name/description/valuetext go on the thumb,
 * which is the focusable `role="slider"`. Arrow keys step, PageUp/PageDown jump, Home/End go to the ends.
 */
export const Slider = forwardRef<HTMLSpanElement, SliderProps>(function Slider(
  {
    value,
    defaultValue,
    onValueChange,
    onValueCommit,
    "aria-label": ariaLabel,
    "aria-labelledby": ariaLabelledBy,
    "aria-describedby": ariaDescribedBy,
    "aria-valuetext": ariaValueText,
    tone = "accent",
    className,
    ...props
  },
  ref,
) {
  return (
    <RadixSlider.Root
      ref={ref}
      value={value === undefined ? undefined : [value]}
      defaultValue={defaultValue === undefined ? undefined : [defaultValue]}
      onValueChange={onValueChange ? ([next]) => next !== undefined && onValueChange(next) : undefined}
      onValueCommit={onValueCommit ? ([next]) => next !== undefined && onValueCommit(next) : undefined}
      className={cn(
        "relative flex h-5 w-full touch-none items-center select-none data-[disabled]:opacity-50",
        className,
      )}
      {...props}
    >
      <RadixSlider.Track className="relative h-1.5 grow overflow-hidden rounded-full bg-surface-2">
        <RadixSlider.Range className={cn("absolute h-full rounded-full", RANGE[tone])} />
      </RadixSlider.Track>
      <RadixSlider.Thumb
        aria-label={ariaLabel}
        aria-labelledby={ariaLabelledBy}
        aria-describedby={ariaDescribedBy}
        aria-valuetext={ariaValueText}
        className={cn(
          "block size-4 cursor-grab rounded-full border-2 bg-surface shadow-subtle transition-colors",
          "hover:bg-surface-2 active:cursor-grabbing data-[disabled]:cursor-not-allowed",
          "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
          THUMB[tone],
        )}
      />
    </RadixSlider.Root>
  );
});
