import { forwardRef, useId } from "react";

import { cn } from "@/lib/utils";

const CONTROL =
  "w-full rounded-sm border border-strong bg-surface px-2.5 text-sm text-fg placeholder:text-muted " +
  "hover:border-control focus-visible:border-accent-strong focus-visible:outline-none focus-visible:ring-2 " +
  "focus-visible:ring-accent/30 disabled:opacity-50 aria-[invalid=true]:border-danger";

export const Input = forwardRef<HTMLInputElement, React.InputHTMLAttributes<HTMLInputElement>>(function Input(
  { className, ...props },
  ref,
) {
  return <input ref={ref} className={cn(CONTROL, "h-8", className)} {...props} />;
});

export type TextareaVariant = "default" | "bare";

export interface TextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  /**
   * "default": a bordered field. "bare": no border, background or ring, for a textarea embedded in a
   * container that draws them itself (e.g. the command bar's focus-within frame).
   */
  variant?: TextareaVariant;
}

const BARE =
  "min-w-0 bg-transparent text-sm leading-5 text-fg placeholder:text-muted focus-visible:outline-none disabled:opacity-50";

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(function Textarea(
  { className, variant = "default", ...props },
  ref,
) {
  return (
    <textarea
      ref={ref}
      className={cn(variant === "bare" ? BARE : cn(CONTROL, "min-h-20 py-2 leading-5"), className)}
      {...props}
    />
  );
});

export interface SelectOption {
  value: string;
  label: string;
  disabled?: boolean;
}

export const Select = forwardRef<
  HTMLSelectElement,
  React.SelectHTMLAttributes<HTMLSelectElement> & { options: SelectOption[] }
>(function Select({ className, options, ...props }, ref) {
  return (
    <select ref={ref} className={cn(CONTROL, "h-8 pr-7", className)} {...props}>
      {options.map((o) => (
        <option key={o.value} value={o.value} disabled={o.disabled}>
          {o.label}
        </option>
      ))}
    </select>
  );
});

export interface FieldProps {
  label: string;
  description?: string;
  error?: string;
  className?: string;
  children: (ids: { id: string; describedBy: string | undefined; invalid: boolean }) => React.ReactNode;
}

/** Label + control + description/error, with the ARIA wiring done once. */
export function Field({ label, description, error, className, children }: FieldProps) {
  const id = useId();
  const descriptionId = description ? `${id}-description` : undefined;
  const errorId = error ? `${id}-error` : undefined;
  const describedBy = [descriptionId, errorId].filter(Boolean).join(" ") || undefined;
  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <label htmlFor={id} className="text-xs font-medium text-fg-secondary">
        {label}
      </label>
      {children({ id, describedBy, invalid: Boolean(error) })}
      {description ? (
        <p id={descriptionId} className="text-xs text-muted">
          {description}
        </p>
      ) : null}
      {error ? (
        <p id={errorId} className="text-xs text-danger-fg">
          {error}
        </p>
      ) : null}
    </div>
  );
}
