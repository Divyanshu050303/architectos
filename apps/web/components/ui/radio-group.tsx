"use client";

import { RadioGroup as RadixRadioGroup } from "radix-ui";
import { useId } from "react";

import { cn } from "@/lib/utils";

export type RadioGroupVariant = "default" | "segmented";

export interface RadioOption<T extends string = string> {
  value: T;
  label: React.ReactNode;
  /** Decorative icon shown before the label (segmented variant). */
  icon?: React.ReactNode;
  description?: string;
  disabled?: boolean;
}

export interface RadioGroupProps<T extends string = string> extends Omit<
  React.ComponentProps<typeof RadixRadioGroup.Root>,
  "children" | "value" | "defaultValue" | "onValueChange" | "ref"
> {
  /** Visible group label; also the radiogroup's accessible name. */
  label: string;
  /** Visually hide the label (it stays the accessible name). */
  hideLabel?: boolean;
  options: ReadonlyArray<RadioOption<T>>;
  value?: T;
  defaultValue?: T;
  onValueChange?: (value: T) => void;
  /** "default": stacked radios. "segmented": a single-choice segmented control. */
  variant?: RadioGroupVariant;
  /** Classes for the element holding the items (e.g. a grid for the segmented variant). */
  itemsClassName?: string;
}

/**
 * Single choice from a small set (spec §73). One component, two presentations: stacked radios or a
 * segmented control. Radix gives roving focus: Tab enters the group, arrow keys move and select.
 */
export function RadioGroup<T extends string = string>({
  label,
  hideLabel = false,
  options,
  value,
  defaultValue,
  onValueChange,
  variant = "default",
  className,
  itemsClassName,
  ...props
}: RadioGroupProps<T>) {
  const id = useId();
  const labelId = `${id}-label`;
  const segmented = variant === "segmented";
  return (
    <div className={cn("flex min-w-0 flex-col gap-1.5", className)}>
      <span id={labelId} className={cn("text-xs font-medium text-fg-secondary", hideLabel && "sr-only")}>
        {label}
      </span>
      <RadixRadioGroup.Root
        aria-labelledby={labelId}
        value={value}
        defaultValue={defaultValue}
        // Radix types the value as string; every item value is a T, so this narrows safely.
        onValueChange={onValueChange ? (next) => onValueChange(next as T) : undefined}
        className={cn(
          segmented
            ? "grid auto-cols-fr grid-flow-col gap-px overflow-hidden rounded-sm border border-strong bg-strong"
            : "flex flex-col gap-2",
          itemsClassName,
        )}
        {...props}
      >
        {options.map((option) => {
          const itemId = `${id}-${option.value}`;
          const descriptionId = option.description ? `${itemId}-description` : undefined;
          if (segmented) {
            return (
              <RadixRadioGroup.Item
                key={option.value}
                value={option.value}
                disabled={option.disabled}
                aria-describedby={descriptionId}
                className={cn(
                  "flex h-8 min-w-0 items-center justify-center gap-1.5 bg-surface px-2 text-center text-xs text-fg-secondary",
                  "transition-colors hover:text-fg disabled:cursor-not-allowed disabled:opacity-50",
                  "focus-visible:z-10 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-ring",
                  "data-[state=checked]:bg-accent-soft data-[state=checked]:font-medium data-[state=checked]:text-accent-fg",
                  "[&_svg]:size-3.5 [&_svg]:shrink-0",
                )}
              >
                {option.icon}
                <span className="truncate">{option.label}</span>
                {option.description ? (
                  <span id={descriptionId} className="sr-only">
                    {option.description}
                  </span>
                ) : null}
              </RadixRadioGroup.Item>
            );
          }
          return (
            <div key={option.value} className="flex items-start gap-2 has-[:disabled]:opacity-50">
              <RadixRadioGroup.Item
                id={itemId}
                value={option.value}
                disabled={option.disabled}
                aria-describedby={descriptionId}
                className={cn(
                  "mt-0.5 inline-flex size-4 shrink-0 items-center justify-center rounded-full border border-control bg-surface",
                  "transition-colors hover:border-accent-strong disabled:cursor-not-allowed",
                  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring",
                  "data-[state=checked]:border-accent-strong",
                )}
              >
                <RadixRadioGroup.Indicator className="block size-2 rounded-full bg-accent-strong" />
              </RadixRadioGroup.Item>
              <div className="flex min-w-0 flex-col">
                <label
                  htmlFor={itemId}
                  className="inline-flex cursor-pointer items-center gap-1.5 text-sm text-fg"
                >
                  {option.icon}
                  {option.label}
                </label>
                {option.description ? (
                  <span id={descriptionId} className="text-xs text-muted">
                    {option.description}
                  </span>
                ) : null}
              </div>
            </div>
          );
        })}
      </RadixRadioGroup.Root>
    </div>
  );
}
